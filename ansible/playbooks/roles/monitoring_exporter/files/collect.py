#!/usr/bin/python3
"""Read-only HOU1 aggregates for node exporter's textfile collector."""
import grp
import json
import math
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import time

SQL = """BEGIN READ ONLY;
SELECT json_build_object('connections',numbackends,'commits',xact_commit,
'rollbacks',xact_rollback,'blocks_read',blks_read,'blocks_hit',blks_hit,
'deadlocks',deadlocks,'database_size',pg_database_size(current_database()))
FROM pg_stat_database WHERE datname=current_database(); COMMIT;"""
INSPECT = '''{"running":{{json .State.Running}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}"none"{{end}},"labels":{{json .Config.Labels}}}'''


def validate_config(config):
    if config.get('environment') not in ('test', 'production'):
        raise ValueError('Unknown monitoring environment')
    containers = config.get('containers')
    if not isinstance(containers, dict) or 'postgresql' not in containers:
        raise ValueError('Missing aggregate sources')
    for label, source in containers.items():
        if (not re.fullmatch(r'[a-z0-9-]+', label) or not isinstance(source, dict)
                or set(source) != {'directory', 'service'}
                or not re.fullmatch(r'/[a-zA-Z0-9_/-]+', source['directory'])
                or '..' in Path(source['directory']).parts
                or not re.fullmatch(r'[a-z0-9-]+', source['service'])):
            raise ValueError('Invalid Compose selector')
    for key in ('authentik_backup_directory', 'request_metrics_log'):
        value = config.get(key, '')
        if not isinstance(value, str) or not re.fullmatch(r'/[a-zA-Z0-9_./-]+', value) or '..' in Path(value).parts:
            raise ValueError('Invalid aggregate path')
    if not isinstance(config.get('controller_tls_enabled', False), bool):
        raise ValueError('Invalid TLS metrics flag')
    if not isinstance(config.get('request_metrics_enabled'), bool):
        raise ValueError('Invalid request metrics flag')


def source_container(source):
    # Include stopped containers: duplicates/stale replacements must fail closed.
    ids = command(['docker', 'ps', '--all', '--quiet', '--no-trunc',
                   '--filter', 'label=com.docker.compose.project.working_dir=' + source['directory'],
                   '--filter', 'label=com.docker.compose.service=' + source['service']]).split()
    if len(ids) != 1 or not re.fullmatch(r'[a-f0-9]{64}', ids[0]):
        raise ValueError('Expected exactly one Compose service container')
    state = json.loads(command(['docker', 'inspect', '--format', INSPECT, ids[0]]))
    labels = state['labels']
    if (labels.get('com.docker.compose.project.working_dir') != source['directory']
            or labels.get('com.docker.compose.service') != source['service']
            or not labels.get('com.docker.compose.project')
            or labels.get('com.docker.compose.oneoff', '').lower() != 'false'):
        raise ValueError('Unexpected Compose identity')
    return ids[0], state



def command(argv):
    return subprocess.run(argv,capture_output=True,text=True,check=True,timeout=10).stdout


def reopen_nginx(config):
    container, state = source_container(config['containers']['nginx'])
    if not state['running']:
        raise ValueError('Nginx is stopped')
    command(['docker', 'exec', container, 'nginx', '-s', 'reopen'])


def collect(config, now=None):
    now=time.time() if now is None else now
    samples=[];success=True;resolved={}
    def emit(name,value,**labels):
        tags='{'+','.join(k+'='+json.dumps(v) for k,v in sorted(labels.items()))+'}' if labels else ''
        samples.append(name+tags+' '+str(float(value)))
    for label,item in config['containers'].items():
        running=healthy=defined=0
        try:
            container,state=source_container(item)
            resolved[label]=container
            running=int(state['running']);defined=int(state['health']!='none')
            healthy=int(running and state['health'] in ('healthy','none'))
        except Exception:
            success=False
        emit('hou1_container_running',running,service=label)
        emit('hou1_container_healthy',healthy,service=label)
        emit('hou1_container_healthcheck_defined',defined,service=label)
    database_up=0
    try:
        rows=command(['docker','exec',resolved['postgresql'],
            'psql','-XqAt','-v','ON_ERROR_STOP=1','-U','authentik','-d','authentik','-c',SQL])
        data=json.loads(rows.strip())
        fields={'connections':('hou1_postgresql_connections','gauge'),
                'database_size':('hou1_postgresql_database_size_bytes','gauge'),
                'commits':('hou1_postgresql_commits_total','counter'),
                'rollbacks':('hou1_postgresql_rollbacks_total','counter'),
                'blocks_read':('hou1_postgresql_blocks_read_total','counter'),
                'blocks_hit':('hou1_postgresql_blocks_hit_total','counter'),
                'deadlocks':('hou1_postgresql_deadlocks_total','counter')}
        for key,(metric,kind) in fields.items():
            if not isinstance(data[key],(int,float)) or data[key]<0:raise ValueError('Invalid aggregate')
            samples.append('# TYPE '+metric+' '+kind);emit(metric,data[key])
        database_up=1
    except Exception:
        success=False
    emit('hou1_postgresql_up',database_up)
    directory=Path(config['authentik_backup_directory'])
    latest=failed=0
    for marker in directory.glob('authentik-*.tar.gz.age.success'):
        archive=Path(str(marker)[:-len('.success')])
        if archive.is_file() and archive.stat().st_size>0 and Path(str(archive)+'.sha256').is_file():
            latest=max(latest,marker.stat().st_mtime)
    for marker in directory.glob('authentik-*.failed'):
        failed=max(failed,marker.stat().st_mtime)
    # A source export proves local capture only. HOU4 owns the off-host receipt/freshness checks.
    if Path('/etc/hou1-backup/source.json').is_file():
        try:
            export = json.loads(Path('/var/lib/hou1-backup/last-export.json').read_text())
            latest = float(export['last_success'])
            failed = max(float(export['attempted']), latest + 1) if not export['last_run_success'] else 0
        except Exception:
            latest, failed = 0, now
    emit('hou1_backup_last_success_timestamp_seconds',latest,backup='authentik-local')
    emit('hou1_backup_last_attempt_success',int(latest>0 and latest>=failed),backup='authentik-local')
    if config.get('request_metrics_enabled'):
        request_success = 0
        try:
            import request_metrics
            samples.append(request_metrics.collect(config['request_metrics_log'], '/var/lib/hou1-monitoring/requests.json').rstrip())
            request_success = 1
        except Exception:
            success = False
        emit('hou1_request_metrics_collection_success', request_success)
    if config.get('controller_tls_enabled'):
        metrics,tls_success=renewal_metrics(config['environment'],now)
        samples.append(metrics.rstrip());success=success and tls_success
    emit('hou1_metrics_collection_success',int(success))
    emit('hou1_metrics_collection_timestamp_seconds',now)
    return '\n'.join(samples)+'\n',success


def renewal_metrics(environment, now=None, path='/var/lib/hou1-tls/controller-status.json'):
    now=time.time() if now is None else now
    values={'last_success':0,'last_run_success':0,'observed_at':0,'certificate_not_after':0}
    success=False
    try:
        data=json.loads(Path(path).read_text())
        if data['environment']!=environment:raise ValueError('Wrong renewal environment')
        for key in ('last_success','observed_at','certificate_not_after'):
            if type(data[key]) not in (int,float) or not math.isfinite(data[key]) or data[key]<0:
                raise ValueError('Invalid renewal metric')
        if (data['last_success']>data['observed_at'] or data['observed_at']>now+30
                or type(data['last_run_success']) is not int or data['last_run_success'] not in (0,1)):
            raise ValueError('Invalid renewal status')
        values=data;success=True
    except Exception:pass
    names={'last_success':'last_success_timestamp_seconds','last_run_success':'last_run_success',
           'observed_at':'metrics_timestamp_seconds','certificate_not_after':'certificate_not_after_timestamp_seconds'}
    return ''.join('hou1_tls_'+names[key]+' '+str(float(values[key]))+'\n' for key in names),success


def write(path,text):
    path=Path(path)
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,prefix='.collect-',delete=False) as stream:
        temporary=Path(stream.name)
        try:
            stream.write(text);stream.flush();os.fchmod(stream.fileno(),0o640)
            os.fchown(stream.fileno(),0,grp.getgrnam('prometheus').gr_gid)
            temporary.replace(path)
        finally:temporary.unlink(missing_ok=True)


if __name__=='__main__':
    try:
        config=json.loads(Path('/etc/hou1-monitoring/collector.json').read_text())
        validate_config(config)
        if sys.argv[1:] == ['--reopen-nginx']:
            reopen_nginx(config)
            raise SystemExit(0)
        if sys.argv[1:]:
            raise ValueError('Unknown collector action')
        text,success=collect(config)
        write('/var/lib/prometheus/node-exporter/hou1.prom',text)
        if not success:raise RuntimeError('A collector source failed')
    except Exception:
        print('HOU1 metrics collection failed; service details withheld.',file=sys.stderr)
        raise SystemExit(1)
