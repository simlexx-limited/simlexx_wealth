#!/usr/bin/env bash

cd ~/simlexx_wealth
docker compose exec -T postgres pg_dump -U simlexx_wealth -d simlexx_wealth_db -t products --data-only -Fc \
  | docker run --rm -i postgres:16-alpine pg_restore --no-owner --no-acl --data-only -d "postgresql://simlexx:PjshvCYCbdbd6dfshSGIH7fKgJjIHezh@dpg-dbcdfh0n74is7386dcj0-a.oregon-postgres.render.com/simlexx_wealth_db"
