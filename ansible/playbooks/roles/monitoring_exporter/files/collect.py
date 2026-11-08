#!/usr/bin/python3
"""Read-only HOU1 aggregates for node exporter's textfile collector."""
import grp
import json
import os
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
INSPECT = '''{"running":{{json .State.Running}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}"none"{{end}},"project":{{json (index .Config.Labels "com.docker.compose.project")}}}'''


def command(argv):
    return subprocess.run(argv,capture_output=True,text=True,check=True,timeout=10).stdout


def collect(config, now=None):
    now=time.time() if now is None else now
    samples=[];success=True
    def emit(name,value,**labels):
        tags='{'+','.join(k+'='+json.dumps(v) for k,v in sorted(labels.items()))+'}' if labels else ''
        samples.append(name+tags+' '+str(float(value)))
    for label,item in config['containers'].items():
        running=healthy=defined=0
        try:
            state=json.loads(command(['docker','inspect','--format',INSPECT,item['name']]))
            if state['project']!=item['project']:raise ValueError('Unexpected container project')
            running=int(state['running']);defined=int(state['health']!='none')
            healthy=int(running and state['health'] in ('healthy','none'))
        except Exception:
            success=False
        emit('hou1_container_running',running,service=label)
        emit('hou1_container_healthy',healthy,service=label)
        emit('hou1_container_healthcheck_defined',defined,service=label)
    database_up=0
    try:
        rows=command(['docker','exec',config['containers']['postgresql']['name'],
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
    emit('hou1_metrics_collection_success',int(success))
    emit('hou1_metrics_collection_timestamp_seconds',now)
    return '\n'.join(samples)+'\n',success


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
        if config['environment']!='test':raise ValueError('Only isolated test data is authorized')
        text,success=collect(config)
        write('/var/lib/prometheus/node-exporter/hou1.prom',text)
        if not success:raise RuntimeError('A collector source failed')
    except Exception:
        print('HOU1 metrics collection failed; service details withheld.',file=sys.stderr)
        raise SystemExit(1)
