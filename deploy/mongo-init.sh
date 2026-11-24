#!/bin/bash
# Runs once on first MongoDB start: creates the application user in the game database.
set -e
mongosh --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin <<JS
db.getSiblingDB("$TFM_DB_NAME").createUser({user: "$TFM_DB_USERNAME", pwd: "$TFM_DB_PASSWORD", roles: [{role: "readWrite", db: "$TFM_DB_NAME"}]});
JS
