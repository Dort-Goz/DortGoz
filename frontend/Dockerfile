# syntax=docker/dockerfile:1
# The frozen Bun lockfile is installed while preparing the pre-air-gap image.
FROM oven/bun:1.3.14

WORKDIR /app
COPY frontend/package.json frontend/bun.lock ./
RUN bun install --frozen-lockfile
COPY frontend ./
RUN bun run build

EXPOSE 5173
CMD ["bun", "run", "preview", "--", "--host", "0.0.0.0", "--port", "5173"]
