import argparse
import os
import subprocess
import threading
import time
import json
import shutil
from collections import defaultdict

class ProcessManager:
    def __init__(self, repo_path):
        self.repo_path = repo_path
        self.units = {}
        self.processes = {}
        self.running = False
        self.init_repo()

    def init_repo(self):
        if not os.path.exists(self.repo_path):
            os.makedirs(self.repo_path)
        os.chdir(self.repo_path)
        try:
            subprocess.run(["git", "init"], check=True, capture_output=True)
            subprocess.run(["git", "checkout", "-b", "main"], check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            print(f"Failed to init repo: {e}")
            raise

    def load_units(self):
        self.units = {}
        for root, _, files in os.walk(self.repo_path):
            for f in files:
                if f.endswith(".service"):
                    path = os.path.join(root, f)
                    unit = {}
                    with open(path, 'r') as uf:
                        for line in uf:
                            if '=' in line:
                                key, value = line.strip().split('=', 1)
                                unit[key.lower()] = value
                    self.units[os.path.relpath(path, self.repo_path)] = unit

    def start_unit(self, unit_path):
        unit = self.units.get(unit_path)
        if not unit or 'execstart' not in unit:
            print(f"Invalid unit {unit_path}")
            return
        cmd = unit['execstart'].split()
        try:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.processes[unit_path] = {
                'pid': process.pid,
                'process': process,
                'status': 'running',
                'restart_count': 0
            }
            print(f"Started {unit_path} with PID {process.pid}")
            self.commit_change(unit_path, "started")
        except Exception as e:
            print(f"Failed to start {unit_path}: {e}")

    def stop_unit(self, unit_path):
        if unit_path in self.processes and self.processes[unit_path]['process']:
            self.processes[unit_path]['process'].terminate()
            self.processes[unit_path]['process'].wait()
            del self.processes[unit_path]
            self.commit_change(unit_path, "stopped")

    def restart_unit(self, unit_path):
        self.stop_unit(unit_path)
        self.start_unit(unit_path)

    def monitor_units(self):
        while self.running:
            for unit_path, info in list(self.processes.items()):
                if info['process'].poll() is not None:
                    exit_code = info['process'].returncode
                    if exit_code != 0 and info['restart_count'] < 3:
                        info['restart_count'] += 1
                        self.start_unit(unit_path)
                        self.commit_change(unit_path, f"restarted (exit {exit_code}, attempt {info['restart_count']})")
                    else:
                        del self.processes[unit_path]
                        self.commit_change(unit_path, f"stopped (exit {exit_code})")
            time.sleep(1)

    def commit_change(self, unit_path, action):
        os.chdir(self.repo_path)
        try:
            subprocess.run(["git", "add", unit_path], check=True, capture_output=True)
            msg = f"{action} {unit_path} at {datetime.datetime.utcnow().isoformat()}Z"
            subprocess.run(["git", "commit", "-m", msg], check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            print(f"Commit failed: {e}")

    def resolve_dependencies(self, unit_path):
        unit = self.units.get(unit_path, {})
        deps = unit.get('after', '').split()
        return deps

    def start_with_deps(self, unit_path):
        deps = self.resolve_dependencies(unit_path)
        for dep in deps:
            dep_path = next((k for k in self.units if dep in k), None)
            if dep_path and dep_path not in self.processes:
                self.start_with_deps(dep_path)
        if unit_path not in self.processes:
            self.start_unit(unit_path)

    def health_check(self, unit_path):
        unit = self.units.get(unit_path, {})
        if 'healthurl' in unit:
            try:
                subprocess.run(["curl", "-s", "-f", unit['healthurl']], check=True, capture_output=True)
                return True
            except subprocess.CalledProcessError:
                return False
        return True

    def run(self):
        self.running = True
        monitor_thread = threading.Thread(target=self.monitor_units)
        monitor_thread.start()
        while self.running:
            time.sleep(1)
        monitor_thread.join()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process Manager")
    parser.add_argument("--repo", required=True, help="Path to Git repo")
    parser.add_argument("--action", choices=['start', 'stop', 'restart'], required=True)
    parser.add_argument("--unit", required=True, help="Unit file path (relative to repo)")
    args = parser.parse_args()

    manager = ProcessManager(args.repo)
    manager.load_units()

    if args.action == "start":
        manager.start_with_deps(args.unit)
    elif args.action == "stop":
        manager.stop_unit(args.unit)
    elif args.action == "restart":
        manager.restart_unit(args.unit)

    manager.run()