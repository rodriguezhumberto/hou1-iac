#!/usr/bin/python3
"""Transfer the host listener between Compose projects as one local operation."""
import argparse
import subprocess


def compose(directory, project='', compose_file=''):
    command = ['docker', 'compose', '--project-directory', directory]
    if project:
        command += ['--project-name', project]
    if compose_file:
        command += ['--file', compose_file]
    return command


def run(command):
    return subprocess.run(command, check=True, timeout=900)


def cutover(args):
    legacy = compose(args.legacy_directory, args.legacy_project, args.legacy_file)
    independent = compose(args.independent_directory, args.independent_project,
                          args.independent_file)
    try:
        run(legacy + ['--profile', 'integrated-ingress-disabled', 'stop', 'nginx'])
        run(['/usr/local/libexec/hou1-compose-apply', args.independent_directory,
             '--project', args.independent_project, '--file', args.independent_file,
             '--validate-nginx'])
    except Exception:
        # Release 443 before restoring the legacy listener.  This is also safe
        # when Compose failed before creating the independent container.
        try:
            run(independent + ['stop', 'nginx'])
        finally:
            run(legacy + ['--profile', 'integrated-ingress-disabled', 'up', '-d',
                          '--no-deps', 'nginx'])
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-directory', required=True)
    parser.add_argument('--legacy-project', default='')
    parser.add_argument('--legacy-file', default='')
    parser.add_argument('--independent-directory', required=True)
    parser.add_argument('--independent-project', required=True)
    parser.add_argument('--independent-file', required=True)
    cutover(parser.parse_args())


if __name__ == '__main__':
    main()
