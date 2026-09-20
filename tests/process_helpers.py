"""Stop only a fixture-owned subprocess, including Windows venv launchers."""
import os
import subprocess


def kill_fixture_process(process):
    if process.poll() is None:
        if os.name == 'nt':
            # A Windows venv python.exe launches a distinct interpreter PID.
            # Killing only Popen.pid leaves that interpreter and its OS locks alive.
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
        else:
            process.kill()
    process.wait(timeout=10)
