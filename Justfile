# Install / sync dependencies
sync:
    uv sync

# Run the scraper
run:
    uv run python auto_trader.py

# Clean generated output files
clean:
    rm -f autotrader_info_*.xlsx error_log.txt
    rm -f scraper.log

# Show outdated dependencies
outdated:
    uv tree --outdated

# Update all dependencies to latest compatible versions
update:
    uv lock --upgrade && uv sync

#####

# Lint (report only)
lint:
    uv run ruff check .

# Apply auto-fixable lint issues and format
fix:
    uv run ruff check --fix .
    uv run ruff format .

# Check formatting without writing changes
fmt-check:
    uv run ruff format --check .

# Type-check API
typecheck:
    uv run ty check .

# Run dependency security scan
pysentry:
    uv run pysentry-rs .

# Run SAST scan
semgrep:
    semgrep --config auto api/

# Run all static-analysis checks
check: lint fmt-check typecheck
