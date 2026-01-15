# Default shell
set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

# Variables
service := "web"

# List available recipes
default:
	@just --list

# Build the Docker image
init:
	docker compose build

# Start the stack (detached)
start:
	docker compose up --build -d

# Stop the stack
stop:
	docker compose down --remove-orphans

# Restart the stack
restart:
	echo "rebooting... beep boop..."
	docker compose down --remove-orphans
	docker compose up --build -d

# View logs from the web service
logs:
	docker compose logs -f {{service}}

# Shell into the web container
sh:
	docker compose exec {{service}} bash || docker compose exec {{service}} sh

# Connect to Postgres via psql
psql:
	docker compose exec db psql -U postgres -d herald

# Run tests (backend + e2e)
# Usage:
#   just test                  # run backend + e2e
#   just test py_args="-k join"        # pass flags to pytest
#   just test e2e_args="--headed"      # pass flags to Playwright
#   just test E2E=0                    # skip e2e
test py_args="" e2e_args="":
	uv run pytest tests/api {{py_args}}
	if [ "${E2E:-1}" -eq 1 ]; then npm run test:e2e -- {{e2e_args}}; fi
