# Extreme AP Dashboard - PostgreSQL Edition

A lightweight Python-based monitoring solution for Extreme Networks Access Points using PostgreSQL instead of Prometheus/Grafana for better performance with 20,000+ APs.

## Features

- **Lightweight**: Uses PostgreSQL instead of Prometheus/Grafana to reduce memory and CPU usage
- **Fast Search**: Search APs by hostname, IP address, MAC address, or serial number
- **Real-time Monitoring**: 10-minute polling interval with controller and AP status tracking
- **Failure Tracking**: Records first failure timestamps and failure reasons
- **Clean UI**: Light-themed dashboard similar to the original Grafana design
- **Scalable**: Handles 20,000+ APs efficiently with parallel processing

## Architecture

- **exporter.py**: Background service that polls controllers every 10 minutes and stores data in PostgreSQL
- **app.py**: Flask web application serving the dashboard UI
- **PostgreSQL**: Database for storing controller and AP information

## Quick Start with Docker Compose

### 1. Prerequisites

- Docker and Docker Compose installed
- `targets.csv` file with controller credentials

### 2. Create targets.csv

Create a `targets.csv` file in the same directory:

```csv
ip_address,username,password,host_name
192.168.1.100,admin,password123,Controller-Site1
192.168.1.101,admin,password123,Controller-Site2
```

### 3. Deploy

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down

# Stop and remove data
docker-compose down -v
```

### 4. Access Dashboard

Open your browser and navigate to:
- **URL**: http://localhost:8080
- **Default Username**: admin
- **Default Password**: admin

## Manual Installation

### 1. Install PostgreSQL

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install postgresql postgresql-contrib

# Start PostgreSQL
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Create database
sudo -u postgres psql
CREATE DATABASE extreme_aps;
CREATE USER postgres WITH PASSWORD 'postgres';
GRANT ALL PRIVILEGES ON DATABASE extreme_aps TO postgres;
\q
```

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables

```bash
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=extreme_aps
export DB_USER=postgres
export DB_PASSWORD=postgres
export SECRET_KEY=your-secret-key-here
export ADMIN_USERNAME=admin
export ADMIN_PASSWORD=admin
```

### 4. Run Services

```bash
# Terminal 1 - Run exporter (data collector)
python exporter.py

# Terminal 2 - Run web dashboard
python app.py
```

### 5. Access Dashboard

Open http://localhost:8080 in your browser

## Configuration

### Exporter Settings (exporter.py)

```python
POLLING_INTERVAL = 600        # Poll every 10 minutes
TOKEN_LIFETIME_MINUTES = 110  # Token cache lifetime
REQUEST_TIMEOUT = 10          # API request timeout
MAX_WORKERS = 30              # Parallel workers for polling
```

### Database Configuration

Set these environment variables:

- `DB_HOST`: PostgreSQL host (default: localhost)
- `DB_PORT`: PostgreSQL port (default: 5432)
- `DB_NAME`: Database name (default: extreme_aps)
- `DB_USER`: Database user (default: postgres)
- `DB_PASSWORD`: Database password (default: postgres)

### Web Application Configuration

- `SECRET_KEY`: Flask secret key for sessions
- `ADMIN_USERNAME`: Dashboard login username
- `ADMIN_PASSWORD`: Dashboard login password

## Database Schema

### Controllers Table

```sql
CREATE TABLE controllers (
    target_ip VARCHAR(45) PRIMARY KEY,
    host_name VARCHAR(255),
    status INTEGER DEFAULT 0,
    fail_reason TEXT,
    first_failure_time BIGINT,
    last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Access Points Table

```sql
CREATE TABLE access_points (
    id SERIAL PRIMARY KEY,
    target_ip VARCHAR(45),
    hostname VARCHAR(255),
    serial_number VARCHAR(100) UNIQUE,
    ip_address VARCHAR(45),
    mac_address VARCHAR(17),
    hardware_type VARCHAR(100),
    software_version VARCHAR(100),
    status VARCHAR(50),
    status_value INTEGER,
    floor_name VARCHAR(255),
    host_site VARCHAR(255),
    sys_uptime BIGINT,
    last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (target_ip) REFERENCES controllers(target_ip)
);
```

## Features

### Dashboard Statistics

- Total Controllers
- Failed Controllers
- Total APs
- Failed APs (Not InService)

### Search Functionality

Search APs by:
- Hostname
- IP Address
- MAC Address
- Serial Number
- Controller Name

### AP Details View

Click on any AP hostname to see complete information:
- Status
- Hardware Type
- Software Version
- System Uptime
- Floor Location
- Site Information
- Controller Details

## Performance Optimization

### For 20,000+ APs

1. **Database Indexing**: Automatic indexes on search fields
2. **Batch Inserts**: Uses `execute_values` for bulk operations
3. **Parallel Processing**: 30 concurrent workers for polling
4. **Token Caching**: Reduces authentication overhead
5. **Connection Pooling**: Reuses database connections

### Memory Usage

Typical memory usage:
- PostgreSQL: ~200-500 MB
- Exporter: ~100-200 MB
- Web App: ~50-100 MB

**Total**: ~350-800 MB (vs 2-4 GB for Prometheus/Grafana)

## Monitoring

### Check Exporter Logs

```bash
# Docker
docker-compose logs -f exporter

# Manual
tail -f exporter.log
```

### Check Database Stats

```sql
-- Connect to database
psql -U postgres -d extreme_aps

-- Count APs
SELECT COUNT(*) FROM access_points;

-- Count failed APs
SELECT COUNT(*) FROM access_points WHERE status_value = 0;

-- Check controller status
SELECT target_ip, host_name, status, fail_reason FROM controllers;
```

## Troubleshooting

### Exporter not collecting data

1. Check CSV file format
2. Verify controller credentials
3. Check network connectivity
4. Review exporter logs

### Database connection errors

1. Verify PostgreSQL is running
2. Check database credentials
3. Ensure database exists
4. Check firewall rules

### Web dashboard not loading

1. Check if app.py is running
2. Verify database connection
3. Check port 8080 availability
4. Review web application logs

## Security Recommendations

1. **Change default passwords**: Update admin credentials
2. **Use strong secret key**: Set a random SECRET_KEY
3. **Enable HTTPS**: Use reverse proxy (nginx/Apache)
4. **Firewall**: Restrict database access
5. **Update regularly**: Keep dependencies updated

## Production Deployment

### Using Nginx as Reverse Proxy

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

### Systemd Service

Create `/etc/systemd/system/extreme-exporter.service`:

```ini
[Unit]
Description=Extreme AP Exporter
After=postgresql.service

[Service]
Type=simple
User=extreme
WorkingDirectory=/opt/extreme-ap-dashboard
Environment="DB_HOST=localhost"
Environment="DB_PASSWORD=your-password"
ExecStart=/usr/bin/python3 exporter.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/extreme-web.service`:

```ini
[Unit]
Description=Extreme AP Web Dashboard
After=postgresql.service

[Service]
Type=simple
User=extreme
WorkingDirectory=/opt/extreme-ap-dashboard
Environment="DB_HOST=localhost"
Environment="SECRET_KEY=your-secret-key"
ExecStart=/usr/bin/python3 app.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl enable extreme-exporter extreme-web
sudo systemctl start extreme-exporter extreme-web
```
