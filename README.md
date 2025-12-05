# SFTP Mirror Script

A robust Python script for mirroring remote SFTP paths to local destinations using `lftp`. The script efficiently syncs files and directories with intelligent size comparison, parallel downloads, and comprehensive monitoring.

## Features

- **Efficient Synchronization**: Compares local and remote file/directory sizes using `du` to skip unchanged files
- **Parallel Downloads**: Configurable number of parallel download jobs (default: 3) using `lftp`'s `mget -cdP` feature
- **Smart Ordering**: Downloads sub-directories in reverse alphanumeric order (newest first, e.g., 20251118 before 20251117)
- **Force Download Option**: `-a` flag to re-download all files without size comparison
- **Graceful Shutdown**: Handles SIGTERM/SIGINT signals to cleanly stop downloads and exit
- **Comprehensive Logging**: Logs all operations to syslog/journal
- **Performance Metrics**: Sends InfluxDB line protocol metrics to Telegraf socket for monitoring
- **Systemd Integration**: Includes service and timer unit files for automated scheduling

## Requirements

- Python 3.6+
- `lftp` (LFTP - Sophisticated file transfer program)
- `du` command (standard on Linux systems)
- Systemd (for service/timer functionality)
- Telegraf socket at `/run/telegraf/telegraf.sock` (optional, for metrics)

## Installation

### 1. Install Dependencies

```bash
# On RHEL/CentOS/Rocky Linux
sudo yum install lftp python3

# On Debian/Ubuntu
sudo apt-get install lftp python3
```

### 2. Copy Script to System Location

```bash
sudo cp sftp_mirror.py /usr/local/bin/sftp_mirror.py
sudo chmod +x /usr/local/bin/sftp_mirror.py
```

Or keep it in your project directory and adjust paths in systemd service files accordingly.

## Configuration

### Environment Variables

The script requires two environment variables for SFTP authentication:

- `SFTP_USERNAME`: SFTP username
- `SFTP_PASSWORD`: SFTP password

**Security Note**: Never store credentials in the script or service file directly. Use systemd override files or environment files with appropriate permissions.

### Setting Credentials via Systemd Override

#### Per-User Override (Recommended for Personal Use)

```bash
# Create override directory in user systemd config
mkdir -p ~/.config/systemd/user/sftp-mirror.service.d/

# Copy and edit the example override file
cp sftp-mirror.service.d-override.conf.example ~/.config/systemd/user/sftp-mirror.service.d/override.conf
nano ~/.config/systemd/user/sftp-mirror.service.d/override.conf

# Set proper permissions
chmod 600 ~/.config/systemd/user/sftp-mirror.service.d/override.conf

# Reload user systemd daemon
systemctl --user daemon-reload
```

#### System-Wide Override (Requires Root)

```bash
# Create override directory
sudo mkdir -p /etc/systemd/system/sftp-mirror.service.d/

# Copy and edit the example override file
sudo cp sftp-mirror.service.d-override.conf.example /etc/systemd/system/sftp-mirror.service.d/override.conf
sudo nano /etc/systemd/system/sftp-mirror.service.d/override.conf

# Set proper permissions
sudo chmod 600 /etc/systemd/system/sftp-mirror.service.d/override.conf

# Reload systemd
sudo systemctl daemon-reload
```

## Usage

### Command Line

```bash
export SFTP_USERNAME="your_username"
export SFTP_PASSWORD="your_password"

# Basic usage
./sftp_mirror.py example.com /remote/path /local/path

# With custom parallel jobs
./sftp_mirror.py example.com /remote/path /local/path -j 5

# Force re-download all files
./sftp_mirror.py example.com /remote/path /local/path -a

# Combine options
./sftp_mirror.py example.com /remote/path /local/path -j 5 -a
```

### Command Line Arguments

- `server`: SFTP server hostname or IP address (required)
- `remote_path`: Remote path to mirror (required)
- `local_path`: Local destination path (required)
- `-j, --jobs N`: Number of parallel download jobs (default: 3)
- `-a, --all`: Re-download all files without size comparison

### Exit Codes

- `0`: Success
- `1`: Error (missing credentials, connection failure, etc.)

## Systemd Service Setup

Systemd services can be installed either **per-user** (no root required, recommended for personal use) or **system-wide** (requires root, for system services). Choose the method that best fits your needs.

**Quick Reference:**
- **Per-user**: Files go in `~/.config/systemd/user/`, use `systemctl --user`, runs as your user
- **System-wide**: Files go in `/etc/systemd/system/`, use `sudo systemctl`, runs as root or service user

**Note for per-user services**: To persist after logout, enable lingering:
```bash
loginctl enable-linger $USER
```

### Per-User Installation (Recommended for Personal Use)

Per-user systemd services run under your user account and don't require root privileges. They start automatically when you log in and stop when you log out (unless configured otherwise).

#### 1. Create User Systemd Directory

```bash
mkdir -p ~/.config/systemd/user
```

#### 2. Edit and Install Service File

Edit the service file with your server and paths:

```bash
nano sftp-mirror.service
```

Update the `ExecStart` line (and adjust script path if needed):
```
ExecStart=/usr/bin/python3 sftp_mirror.py example.com /remote/path /local/path
```

Copy to user systemd directory:

```bash
cp sftp-mirror.service ~/.config/systemd/user/
```

#### 3. Set Up Credentials in Override File

```bash
# Create override directory
mkdir -p ~/.config/systemd/user/sftp-mirror.service.d/

# Copy and edit the example override file
cp sftp-mirror.service.d-override.conf.example ~/.config/systemd/user/sftp-mirror.service.d/override.conf
nano ~/.config/systemd/user/sftp-mirror.service.d/override.conf

# Set proper permissions
chmod 600 ~/.config/systemd/user/sftp-mirror.service.d/override.conf
```

#### 4. Reload and Enable (User Service)

```bash
# Reload user systemd daemon
systemctl --user daemon-reload

# Enable the service (optional - if you want it to start on login)
systemctl --user enable sftp-mirror.service

# Run manually
systemctl --user start sftp-mirror.service
systemctl --user status sftp-mirror.service
```

**Note**: For user services to persist after logout, enable lingering:

```bash
loginctl enable-linger $USER
```

### System-Wide Installation (Requires Root)

For system-wide services that run as root or a dedicated service user:

1. **Edit the service file** with your server and paths:

```bash
nano sftp-mirror.service
```

Update the `ExecStart` line:
```
ExecStart=/usr/bin/python3 sftp_mirror.py example.com /remote/path /local/path
```

2. **Set up credentials** in override file (see Configuration section above)

3. **Install and enable**:

```bash
sudo cp sftp-mirror.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sftp-mirror.service
```

4. **Run manually**:

```bash
sudo systemctl start sftp-mirror.service
sudo systemctl status sftp-mirror.service
```

### Automated Scheduling with Timers

The project includes timer units for automated execution:

#### Per-User Timer Installation

**Start Timer (14:30 UTC):**

```bash
# Copy timer file
cp sftp-mirror.timer ~/.config/systemd/user/

# Reload user systemd daemon
systemctl --user daemon-reload

# Enable and start timer
systemctl --user enable sftp-mirror.timer
systemctl --user start sftp-mirror.timer
```

**Stop Timer (23:30 UTC):**

```bash
# Copy stop service and timer files
cp sftp-mirror-stop.service sftp-mirror-stop.timer ~/.config/systemd/user/

# Reload user systemd daemon
systemctl --user daemon-reload

# Enable and start timer
systemctl --user enable sftp-mirror-stop.timer
systemctl --user start sftp-mirror-stop.timer
```

**Timer Management (User Services):**

```bash
# View all timer status
systemctl --user list-timers sftp-mirror* --all

# Check next run time
systemctl --user status sftp-mirror.timer

# View timer logs
journalctl --user -u sftp-mirror.timer -f
```

#### System-Wide Timer Installation

**Start Timer (14:30 UTC):**

```bash
sudo cp sftp-mirror.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sftp-mirror.timer
sudo systemctl start sftp-mirror.timer
```

**Stop Timer (23:30 UTC):**

```bash
sudo cp sftp-mirror-stop.service sftp-mirror-stop.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sftp-mirror-stop.timer
sudo systemctl start sftp-mirror-stop.timer
```

**Timer Management (System Services):**

```bash
# View all timer status
sudo systemctl list-timers sftp-mirror* --all

# Check next run time
sudo systemctl status sftp-mirror.timer

# View timer logs
sudo journalctl -u sftp-mirror.timer -f
```

#### Timer Schedule

- **Start Timer**: Runs daily at **14:30 UTC** to start the mirror service
- **Stop Timer**: Runs daily at **23:30 UTC** to stop the service if still running

Both timers run at exactly the specified times with no randomization.

### Multiple Instances (Template Service)

For multiple mirror jobs (different servers/paths), use the template service:

#### Per-User Template Service

```bash
# Install template to user systemd directory
cp sftp-mirror@.service ~/.config/systemd/user/

# Create instance-specific override
mkdir -p ~/.config/systemd/user/sftp-mirror@backup.service.d/
nano ~/.config/systemd/user/sftp-mirror@backup.service.d/override.conf
```

Example override:
```ini
[Service]
Environment="SFTP_USERNAME=user1"
Environment="SFTP_PASSWORD=pass1"
ExecStart=
ExecStart=/usr/bin/python3 sftp_mirror.py server1.com /backup /local/backup
```

Then reload and run:
```bash
systemctl --user daemon-reload
systemctl --user start sftp-mirror@backup.service
```

#### System-Wide Template Service

```bash
# Install template
sudo cp sftp-mirror@.service /etc/systemd/system/

# Create instance-specific override
sudo mkdir -p /etc/systemd/system/sftp-mirror@backup.service.d/
sudo nano /etc/systemd/system/sftp-mirror@backup.service.d/override.conf
```

Example override:
```ini
[Service]
Environment="SFTP_USERNAME=user1"
Environment="SFTP_PASSWORD=pass1"
ExecStart=
ExecStart=/usr/bin/python3 /usr/local/bin/sftp_mirror.py server1.com /backup /local/backup
```

Then run:
```bash
sudo systemctl daemon-reload
sudo systemctl start sftp-mirror@backup.service
```

## Monitoring and Logging

### View Logs

#### Per-User Service Logs

```bash
# Service logs
journalctl --user -u sftp-mirror.service -f

# All logs with syslog identifier
journalctl --user -t sftp-mirror -f

# Last 100 lines
journalctl --user -u sftp-mirror.service -n 100

# Since yesterday
journalctl --user -u sftp-mirror.service --since yesterday
```

#### System-Wide Service Logs

```bash
# Service logs
sudo journalctl -u sftp-mirror.service -f

# All logs with syslog identifier
sudo journalctl -t sftp-mirror -f

# Last 100 lines
sudo journalctl -u sftp-mirror.service -n 100

# Since yesterday
sudo journalctl -u sftp-mirror.service --since yesterday
```

### Performance Metrics

The script sends metrics to InfluxDB via Telegraf socket at `/run/telegraf/telegraf.sock`:

**Per-file/directory metrics** (`sftp_mirror_download`):
- `bytes`: Bytes downloaded
- `duration_seconds`: Download duration
- `bytes_per_second`: Transfer rate
- Tags: `server`, `type` (file/directory), `item` (filename/dirname)

**Summary metrics** (`sftp_mirror_summary`):
- `files_downloaded`: Number of files/directories downloaded
- `files_skipped`: Number of files/directories skipped
- `bytes_downloaded`: Total bytes downloaded
- `duration_seconds`: Total mirror duration
- `avg_file_duration_seconds`: Average file download duration
- Tags: `server`, `remote_path`

### Example Queries

If using InfluxDB/Grafana:

```sql
-- Total bytes downloaded today
SELECT SUM(bytes_downloaded) FROM sftp_mirror_summary WHERE time >= now() - 24h

-- Average transfer rate
SELECT MEAN(bytes_per_second) FROM sftp_mirror_download WHERE time >= now() - 24h

-- Files downloaded per server
SELECT COUNT(files_downloaded) FROM sftp_mirror_summary GROUP BY server
```

## How It Works

1. **Discovery**: Lists remote files and directories using `lftp ls -la`
2. **Size Comparison**: For each item:
   - Gets remote size using `lftp du -sb`
   - Gets local size using `du -sb`
   - Skips if sizes match (unless `-a` flag is used)
3. **Download Strategy**:
   - Directories are sorted in reverse alphanumeric order (newest first)
   - Directories are downloaded using `lftp mget -cdP{N}` for parallel recursive downloads
   - Files are downloaded individually with resume support (`-c` flag)
4. **Metrics**: After each download, sends performance metrics to Telegraf
5. **Signal Handling**: On SIGTERM/SIGINT, gracefully terminates ongoing downloads

## Troubleshooting

### Connection Issues

```bash
# Test lftp connection manually
lftp sftp://username:password@server.com
lftp> cd /remote/path
lftp> ls
lftp> quit
```

### Permission Issues

```bash
# Check local path permissions
ls -ld /local/path

# Ensure service user has write access
sudo chown -R service_user:service_group /local/path
```

### Service Won't Start

#### Per-User Service

```bash
# Check service status
systemctl --user status sftp-mirror.service

# Check for errors in logs
journalctl --user -u sftp-mirror.service -n 50

# Verify credentials are set
systemctl --user show sftp-mirror.service | grep Environment
```

#### System-Wide Service

```bash
# Check service status
sudo systemctl status sftp-mirror.service

# Check for errors in logs
sudo journalctl -u sftp-mirror.service -n 50

# Verify credentials are set
sudo systemctl show sftp-mirror.service | grep Environment
```

### Script Hangs

```bash
# Check if lftp process is running
ps aux | grep lftp

# Check network connectivity
ping server.com

# Test SFTP port
telnet server.com 22
```

### Metrics Not Appearing

```bash
# Check if Telegraf socket exists
ls -l /run/telegraf/telegraf.sock

# Verify Telegraf is running
sudo systemctl status telegraf

# Check socket permissions
sudo chmod 666 /run/telegraf/telegraf.sock  # Adjust as needed
```

## Security Considerations

1. **Credentials**: Store credentials in systemd override files with `600` permissions
   - Per-user: `~/.config/systemd/user/sftp-mirror.service.d/override.conf`
   - System-wide: `/etc/systemd/system/sftp-mirror.service.d/override.conf`
2. **Service User**: 
   - Per-user services run as your user account (no root needed)
   - For system-wide services, run as a non-root dedicated service user when possible
3. **File Permissions**: Restrict read/write access to destination directories
4. **Network**: Use VPN or firewall rules to restrict SFTP access
5. **Logging**: Be aware that server names and paths appear in logs
6. **User Service Persistence**: Enable lingering if you want user services to continue after logout:
   ```bash
   loginctl enable-linger $USER
   ```

## Examples

### Daily Backup Script

```bash
#!/bin/bash
# Daily backup at 2 AM
export SFTP_USERNAME="backup_user"
export SFTP_PASSWORD="backup_pass"
/usr/local/bin/sftp_mirror.py backup.example.com /data /local/backups -j 5
```

### Multiple Server Sync

Create multiple systemd service instances:

```bash
# Server 1
sudo systemctl start sftp-mirror@server1.service

# Server 2  
sudo systemctl start sftp-mirror@server2.service
```

### Cron Alternative

```bash
# Add to crontab (crontab -e)
0 2 * * * export SFTP_USERNAME="user" SFTP_PASSWORD="pass" && /usr/local/bin/sftp_mirror.py server.com /remote /local
```

## License

This project is licensed under the GNU General Public License v3.0 (GPL-3.0).

See the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request. For major changes, please open an issue first to discuss what you would like to change.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

Please ensure your code follows the existing style and includes appropriate documentation.

## Support

For issues, questions, or feature requests, please open an issue on [GitHub](https://github.com/daniel-trnk/lftp-mirror/issues).

