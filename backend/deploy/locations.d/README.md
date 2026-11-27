# Extra nginx locations

Every `*.conf` here is included inside the HTTPS `server {}` block of
`../nginx.conf` (mounted at `/etc/nginx/locations.d` by
`docker-compose.prod.yml`). Use it to serve other apps from the same host
under their own sub-path, without editing Pikirler's config.

The files are server-specific and are not committed (`*.conf` is ignored).
After adding or changing one:

```sh
docker compose -f docker-compose.prod.yml exec nginx nginx -t
docker compose -f docker-compose.prod.yml exec nginx nginx -s reload
```

Example — an app whose web container joins the `pikirler-prod_internal`
network under the alias `myapp-web` and is built for the `/myapp/` prefix:

```nginx
location = /myapp {
    return 301 /myapp/;
}

location ^~ /myapp/ {
    # A variable makes nginx resolve the name per request (resolver above),
    # so Pikirler still starts when the other app is down.
    set $myapp_web http://myapp-web:80;
    rewrite ^/myapp/(.*)$ /$1 break;
    proxy_pass $myapp_web;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```
