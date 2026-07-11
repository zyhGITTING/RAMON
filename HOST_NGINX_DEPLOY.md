# Host nginx deployment

This package now uses the nginx that already exists on the server.

`docker-compose.yml` starts only:

- `postgres`
- `backend`

The container nginx service has been removed. Do not use the old `18080` entry path for this deployment mode.

## Start backend services

```bash
cd /opt/datamid
docker compose up -d --build
docker compose ps
```

The backend is exposed only on localhost:

```text
127.0.0.1:8128
```

## Configure host nginx

Copy the contents of `server-nginx-data-center.conf` into the existing host nginx `server {}` block.

Replace this path with the real `frontend` parent directory:

```nginx
root /opt/datamid/frontend;
```

For example, if the package is deployed at `/data/apps/datamid`, use:

```nginx
root /data/apps/datamid/frontend;
```

Then test and reload nginx:

```bash
nginx -t
systemctl reload nginx
```

## Access URL

Open:

```text
http://YOUR_SERVER/data_center/
```

API requests such as:

```text
/data_center/api/public/config
```

will be proxied to:

```text
http://127.0.0.1:8128/api/public/config
```
