# BRIMOON Studio Deploy

Production domain:

- `brimoon.es`
- `www.brimoon.es`

The Django backend should expose public URLs with:

```env
SITE_URL=https://brimoon.es
PUBLIC_BASE_URL=https://brimoon.es
APP_DOWNLOAD_URL=https://brimoon.es/app/
```

Do not commit real secrets. Keep production credentials in `.env`.

## Django

`ALLOWED_HOSTS` must include:

- `brimoon.es`
- `www.brimoon.es`
- `127.0.0.1`
- `localhost`

`CSRF_TRUSTED_ORIGINS` must include:

- `https://brimoon.es`
- `https://www.brimoon.es`

If CORS is enabled later, allow the same two HTTPS origins.

## Nginx

Example HTTPS server proxying to local gunicorn:

```nginx
server {
    listen 80;
    server_name brimoon.es www.brimoon.es;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name brimoon.es www.brimoon.es;

    client_max_body_size 20M;

    ssl_certificate /etc/letsencrypt/live/brimoon.es/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/brimoon.es/privkey.pem;

    location /static/ {
        alias /home/seradmin/anna/static/;
        expires 5m;
        add_header Cache-Control "public, must-revalidate";
    }

    location /media/ {
        alias /home/seradmin/anna/media/;
        expires 30d;
        add_header Cache-Control "public";
    }

    location / {
        proxy_pass http://127.0.0.1:8010;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_redirect off;
    }
}
```

Issue or renew HTTPS with certbot:

```bash
sudo certbot --nginx -d brimoon.es -d www.brimoon.es
```

## Release Commands

```bash
cd /home/seradmin/anna
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput
venv/bin/python manage.py check
sudo systemctl restart anna
sudo systemctl reload nginx
```

Run tests when changing backend behavior:

```bash
venv/bin/python manage.py test mobile_api bookings clients employees accounts
```
