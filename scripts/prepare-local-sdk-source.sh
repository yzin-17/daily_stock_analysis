#!/bin/sh
exec node "$(dirname "$0")/prepare-local-sdk-source.mjs" "$@"
