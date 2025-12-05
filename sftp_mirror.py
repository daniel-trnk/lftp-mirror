#!/usr/bin/env python3
"""
SFTP Mirror Script using lftp

Mirrors a remote SFTP path to a local destination with:
- Size comparison to skip unchanged files
- Parallel downloads with configurable jobs
- Reverse alphanumeric directory ordering
- Syslog/journal logging
- InfluxDB metrics via Telegraf socket

Copyright (C) 2024

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import argparse
import os
import subprocess
import sys
import syslog
import socket
import shlex
import urllib.parse
import signal
import time
from pathlib import Path
from typing import List, Dict, Tuple, Optional


class SFTPMirror:
    def __init__(self, server: str, remote_path: str, local_path: str, 
                 username: str, password: str, parallel_jobs: int = 3,
                 force_download: bool = False):
        self.server = server
        self.remote_path = remote_path.rstrip('/')
        self.local_path = Path(local_path)
        self.username = username
        self.password = password
        self.parallel_jobs = parallel_jobs
        self.force_download = force_download
        
        # Ensure local path exists
        self.local_path.mkdir(parents=True, exist_ok=True)
        
        # Statistics
        self.stats = {
            'files_downloaded': 0,
            'files_skipped': 0,
            'bytes_downloaded': 0,
            'start_time': time.time(),
            'file_times': []
        }
        
        # Signal handling
        self.stop_requested = False
        self.current_process = None
    
    def _get_lftp_connection_url(self) -> str:
        """Build lftp connection URL with URL-encoded credentials"""
        # URL-encode username and password
        encoded_user = urllib.parse.quote(self.username, safe='')
        encoded_pass = urllib.parse.quote(self.password, safe='')
        return f"sftp://{encoded_user}:{encoded_pass}@{self.server}"
    
    def _signal_handler(self, signum, frame):
        """Handle termination signals"""
        self.stop_requested = True
        self.log(f"Received signal {signum}, stopping downloads...", 'warning')
        # Terminate current subprocess if any
        if self.current_process:
            try:
                self.current_process.terminate()
                # Give it a moment to terminate gracefully
                try:
                    self.current_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Force kill if it doesn't terminate
                    self.current_process.kill()
                    self.current_process.wait()
            except Exception as e:
                self.log(f"Error terminating subprocess: {e}", 'warning')
    
    def log(self, message: str, level: str = 'info'):
        """Log message to syslog/journal"""
        level_map = {
            'info': syslog.LOG_INFO,
            'warning': syslog.LOG_WARNING,
            'error': syslog.LOG_ERR,
            'debug': syslog.LOG_DEBUG
        }
        syslog.syslog(level_map.get(level, syslog.LOG_INFO), message)
        print(f"[{level.upper()}] {message}")
    
    def send_telegraf_metric(self, measurement: str, fields: Dict[str, float], 
                            tags: Optional[Dict[str, str]] = None):
        """Send metric to InfluxDB via Telegraf socket in line protocol format"""
        try:
            #sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect('/run/telegraf/telegraf.sock')
            
            # Escape InfluxDB line protocol special characters
            def escape_identifier(value):
                """Escape measurement names, tag keys/values: comma, space, equals"""
                return str(value).replace(',', '\\,').replace(' ', '\\ ').replace('=', '\\=')
            
            # Build InfluxDB line protocol: measurement,tag1=val1,tag2=val2 field1=val1,field2=val2 timestamp
            escaped_measurement = escape_identifier(measurement)
            tag_str = ''
            if tags:
                tag_parts = [f"{escape_identifier(k)}={escape_identifier(v)}" for k, v in tags.items()]
                tag_str = ',' + ','.join(tag_parts)
            
            # Field names also need escaping
            field_parts = [f"{escape_identifier(k)}={v}" for k, v in fields.items()]
            field_str = ','.join(field_parts)
            timestamp_ns = int(time.time() * 1e9)
            
            line = f"{escaped_measurement}{tag_str} {field_str} {timestamp_ns}\n"
            
            sock.sendall(line.encode('utf-8'))
            sock.close()
        except Exception as e:
            self.log(f"Failed to send InfluxDB metric: {e}", 'warning')
    
    def get_local_size(self, path: Path) -> Optional[int]:
        """Get size of local file or directory using du command"""
        if not path.exists():
            return None
        
        try:
            result = subprocess.run(
                ['du', '-sb', str(path)],
                capture_output=True,
                text=True,
                check=True
            )
            size = int(result.stdout.split()[0])
            return size
        except subprocess.CalledProcessError:
            return None
        except Exception as e:
            self.log(f"Error getting local size for {path}: {e}", 'warning')
            return None
    
    def get_remote_size(self, remote_item: str, is_directory: bool = False) -> Optional[int]:
        """Get size of remote file or directory"""
        # Escape item name for shell safety
        escaped_item = shlex.quote(remote_item)
        connection_url = self._get_lftp_connection_url()
        
        lftp_script = f"""
set sftp:auto-confirm yes
open {connection_url}
cd {shlex.quote(self.remote_path)}
du -sb {escaped_item}
quit
"""
        
        try:
            result = subprocess.run(
                ['lftp', '-c', lftp_script],
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode != 0:
                # Try alternative: get file size directly for files
                if not is_directory:
                    lftp_script2 = f"""
set sftp:auto-confirm yes
open {connection_url}
cd {shlex.quote(self.remote_path)}
size {escaped_item}
quit
"""
                    result2 = subprocess.run(
                        ['lftp', '-c', lftp_script2],
                        capture_output=True,
                        text=True,
                        timeout=60
                    )
                    if result2.returncode == 0:
                        try:
                            size_str = result2.stdout.strip().split()[0]
                            return int(size_str)
                        except (ValueError, IndexError):
                            pass
                return None
            
            # Parse output - du output format: size path
            lines = result.stdout.strip().split('\n')
            for line in lines:
                line = line.strip()
                if line:
                    parts = line.split(None, 1)  # Split on whitespace, max 1 split
                    if parts and parts[0].isdigit():
                        return int(parts[0])
            
            return None
            
        except Exception as e:
            self.log(f"Error getting remote size for {remote_item}: {e}", 'warning')
            return None
    
    def list_remote_directory(self) -> Tuple[List[str], List[str]]:
        """List remote directory and return (files, directories)"""
        # Use lftp's ls -la to get detailed listing with file types
        escaped_remote_path = shlex.quote(self.remote_path)
        connection_url = self._get_lftp_connection_url()
        lftp_script = f"""
set sftp:auto-confirm yes
open {connection_url}
cd {escaped_remote_path}
ls -la
quit
"""
        try:
            result = subprocess.run(
                ['lftp', '-c', lftp_script],
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode != 0:
                self.log(f"Error listing remote directory: {result.stderr}", 'error')
                return [], []
            
            files = []
            directories = []
            
            # Parse ls -la output
            # Format: permissions links owner group size date time name
            for line in result.stdout.strip().split('\n'):
                line = line.strip()
                if not line or line.startswith('total'):
                    continue
                
                parts = line.split()
                if len(parts) < 9:
                    continue
                
                # Get permissions (first field) and name (last field)
                perms = parts[0]
                name = ' '.join(parts[8:])  # Handle names with spaces
                
                # Skip . and ..
                if name in ['.', '..']:
                    continue
                
                # Check if directory (starts with 'd' in permissions)
                if perms.startswith('d'):
                    directories.append(name)
                else:
                    files.append(name)
            
            return files, directories
            
        except Exception as e:
            self.log(f"Error listing remote directory: {e}", 'error')
            return [], []
    
    def download_file(self, remote_file: str, local_file: Path) -> Tuple[bool, int, float]:
        """Download a single file and return (success, bytes, duration)"""
        if self.stop_requested:
            return False, 0, 0.0
        
        start_time = time.time()
        
        # Ensure parent directory exists
        local_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Escape file names for shell safety
        escaped_remote = shlex.quote(remote_file)
        escaped_local = shlex.quote(str(local_file))
        escaped_remote_path = shlex.quote(self.remote_path)
        connection_url = self._get_lftp_connection_url()
        
        lftp_script = f"""
set sftp:auto-confirm yes
set net:timeout 30
open {connection_url}
cd {escaped_remote_path}
get -c {escaped_remote} -o {escaped_local}
quit
"""
        
        try:
            # Use Popen so we can terminate if needed
            self.current_process = subprocess.Popen(
                ['lftp', '-c', lftp_script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            try:
                stdout, stderr = self.current_process.communicate(timeout=3600)
                returncode = self.current_process.returncode
            except subprocess.TimeoutExpired:
                # Terminate if still running
                self.current_process.terminate()
                try:
                    stdout, stderr = self.current_process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    self.current_process.kill()
                    stdout, stderr = self.current_process.communicate()
                returncode = -1
            
            self.current_process = None
            duration = time.time() - start_time
            
            if self.stop_requested:
                return False, 0, duration
            
            if returncode != 0:
                self.log(f"Failed to download {remote_file}: {stderr}", 'error')
                return False, 0, duration
            
            # Get file size
            if local_file.exists():
                size = local_file.stat().st_size
                return True, size, duration
            else:
                return False, 0, duration
                
        except Exception as e:
            if self.current_process:
                try:
                    self.current_process.terminate()
                    self.current_process.wait(timeout=2)
                except:
                    try:
                        self.current_process.kill()
                    except:
                        pass
                self.current_process = None
            duration = time.time() - start_time
            self.log(f"Error downloading {remote_file}: {e}", 'error')
            return False, 0, duration
    
    def download_directory(self, remote_dir: str, local_dir: Path) -> bool:
        """Download a directory recursively with parallel jobs"""
        if self.stop_requested:
            return False
        
        # Create local directory
        local_dir.mkdir(parents=True, exist_ok=True)
        
        # Escape paths for shell safety
        escaped_dir = shlex.quote(remote_dir)
        escaped_remote_path = shlex.quote(self.remote_path)
        escaped_local_dir = shlex.quote(str(local_dir))
        connection_url = self._get_lftp_connection_url()
        
        # Use mget with -cdP{parallel_jobs} format: -c (continue), -d (create dirs), -P{num} (parallel)
        lftp_script = f"""
set sftp:auto-confirm yes
set net:timeout 30
open {connection_url}
cd {escaped_remote_path}/{escaped_dir}
lcd {escaped_local_dir}
mget -cdP{self.parallel_jobs} * .*
quit
"""
        
        try:
            # Use Popen so we can terminate if needed
            self.current_process = subprocess.Popen(
                ['lftp', '-c', lftp_script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            try:
                stdout, stderr = self.current_process.communicate(timeout=7200)
                returncode = self.current_process.returncode
            except subprocess.TimeoutExpired:
                # Terminate if still running
                self.current_process.terminate()
                try:
                    stdout, stderr = self.current_process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    self.current_process.kill()
                    stdout, stderr = self.current_process.communicate()
                returncode = -1
            
            self.current_process = None
            
            if self.stop_requested:
                return False
            
            if returncode != 0:
                self.log(f"Failed to download directory {remote_dir}: {stderr}", 'error')
                return False
            
            return True
            
        except Exception as e:
            if self.current_process:
                try:
                    self.current_process.terminate()
                    self.current_process.wait(timeout=2)
                except:
                    try:
                        self.current_process.kill()
                    except:
                        pass
                self.current_process = None
            self.log(f"Error downloading directory {remote_dir}: {e}", 'error')
            return False
    
    def should_download(self, item_name: str, is_directory: bool) -> bool:
        """Determine if an item should be downloaded based on size comparison"""
        if self.force_download:
            return True
        
        local_path = self.local_path / item_name
        remote_size = self.get_remote_size(item_name, is_directory)
        local_size = self.get_local_size(local_path) if local_path.exists() else None
        
        if local_size is None:
            return True
        
        if remote_size is None:
            self.log(f"Could not get remote size for {item_name}, will download", 'warning')
            return True
        
        return remote_size != local_size
    
    def mirror(self):
        """Main mirror function"""
        self.log(f"Starting mirror: {self.server}{self.remote_path} -> {self.local_path}")
        
        # Get list of files and directories
        files, directories = self.list_remote_directory()
        
        self.log(f"Found {len(files)} files and {len(directories)} directories")
        
        # Sort directories in reverse alphanumeric order (newest first)
        directories.sort(reverse=True)
        
        # Process directories first (in reverse order)
        for remote_dir in directories:
            if self.stop_requested:
                self.log("Stop requested, exiting...", 'warning')
                break
            
            local_dir = self.local_path / remote_dir
            
            if self.should_download(remote_dir, is_directory=True):
                self.log(f"Downloading directory: {remote_dir}")
                download_start = time.time()
                
                success = self.download_directory(remote_dir, local_dir)
                duration = time.time() - download_start
                
                if success:
                    # Get size for stats
                    local_size = self.get_local_size(local_dir)
                    if local_size:
                        self.stats['bytes_downloaded'] += local_size
                        self.stats['files_downloaded'] += 1
                    
                    self.log(f"Downloaded directory {remote_dir} in {duration:.2f}s")
                    
                    # Send performance metric
                    if local_size:
                        self.send_telegraf_metric(
                            'sftp_mirror_download',
                            {
                                'bytes': float(local_size),
                                'duration_seconds': duration,
                                'bytes_per_second': float(local_size) / duration if duration > 0 else 0
                            },
                            tags={
                                'server': self.server,
                                'type': 'directory',
                                'item': remote_dir
                            }
                        )
                else:
                    self.log(f"Failed to download directory {remote_dir}", 'error')
            else:
                self.log(f"Skipping directory {remote_dir} (size matches)")
                self.stats['files_skipped'] += 1
        
        # Process files
        for remote_file in files:
            if self.stop_requested:
                self.log("Stop requested, exiting...", 'warning')
                break
            
            local_file = self.local_path / remote_file
            
            if self.should_download(remote_file, is_directory=False):
                self.log(f"Downloading file: {remote_file}")
                
                success, size, duration = self.download_file(remote_file, local_file)
                
                if success:
                    self.stats['bytes_downloaded'] += size
                    self.stats['files_downloaded'] += 1
                    self.stats['file_times'].append(duration)
                    
                    self.log(f"Downloaded {remote_file} ({size} bytes) in {duration:.2f}s")
                    
                    # Send performance metric
                    self.send_telegraf_metric(
                        'sftp_mirror_download',
                        {
                            'bytes': float(size),
                            'duration_seconds': duration,
                            'bytes_per_second': float(size) / duration if duration > 0 else 0
                        },
                        tags={
                            'server': self.server,
                            'type': 'file',
                            'item': remote_file
                        }
                    )
                else:
                    self.log(f"Failed to download file {remote_file}", 'error')
            else:
                self.log(f"Skipping file {remote_file} (size matches)")
                self.stats['files_skipped'] += 1
        
        # Print final statistics
        total_duration = time.time() - self.stats['start_time']
        avg_duration = sum(self.stats['file_times']) / len(self.stats['file_times']) if self.stats['file_times'] else 0
        
        self.log(f"Mirror complete. Downloaded {self.stats['files_downloaded']} items, "
                f"skipped {self.stats['files_skipped']}, "
                f"total {self.stats['bytes_downloaded']} bytes in {total_duration:.2f}s")
        
        # Send summary metric
        self.send_telegraf_metric(
            'sftp_mirror_summary',
            {
                'files_downloaded': float(self.stats['files_downloaded']),
                'files_skipped': float(self.stats['files_skipped']),
                'bytes_downloaded': float(self.stats['bytes_downloaded']),
                'duration_seconds': total_duration,
                'avg_file_duration_seconds': avg_duration
            },
            tags={
                'server': self.server,
                'remote_path': self.remote_path
            }
        )


def main():
    parser = argparse.ArgumentParser(
        description='Mirror remote SFTP path using lftp'
    )
    parser.add_argument('server', help='SFTP server (hostname or IP)')
    parser.add_argument('remote_path', help='Remote path to mirror')
    parser.add_argument('local_path', help='Local destination path')
    parser.add_argument('-j', '--jobs', type=int, default=3,
                       help='Number of parallel download jobs (default: 3)')
    parser.add_argument('-a', '--all', action='store_true',
                       help='Re-download all files without size comparison')
    
    args = parser.parse_args()
    
    # Get credentials from environment
    username = os.environ.get('SFTP_USERNAME')
    password = os.environ.get('SFTP_PASSWORD')
    
    if not username:
        print("Error: SFTP_USERNAME environment variable not set", file=sys.stderr)
        sys.exit(1)
    
    if not password:
        print("Error: SFTP_PASSWORD environment variable not set", file=sys.stderr)
        sys.exit(1)
    
    # Initialize syslog
    syslog.openlog('sftp_mirror', syslog.LOG_PID, syslog.LOG_DAEMON)
    
    # Create mirror instance and run
    mirror = SFTPMirror(
        server=args.server,
        remote_path=args.remote_path,
        local_path=args.local_path,
        username=username,
        password=password,
        parallel_jobs=args.jobs,
        force_download=args.all
    )
    
    # Set up signal handlers
    def signal_handler(signum, frame):
        mirror._signal_handler(signum, frame)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        mirror.mirror()
        exit_code = 0 if not mirror.stop_requested else 1
        if mirror.stop_requested:
            mirror.log("Mirror stopped by signal", 'warning')
    except KeyboardInterrupt:
        mirror.log("Interrupted by user", 'warning')
        mirror.stop_requested = True
        exit_code = 1
    except Exception as e:
        mirror.log(f"Fatal error: {e}", 'error')
        exit_code = 1
    finally:
        # Clean up any remaining processes
        if mirror.current_process:
            try:
                mirror.current_process.terminate()
                mirror.current_process.wait(timeout=2)
            except:
                try:
                    mirror.current_process.kill()
                except:
                    pass
        syslog.closelog()
        sys.exit(exit_code)


if __name__ == '__main__':
    main()

