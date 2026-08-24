# syntax=docker/dockerfile:1
FROM oven/bun:1.3.14

WORKDIR /home/bun/app
COPY --chown=bun:bun frontend/package.json frontend/bun.lock ./
USER bun
RUN bun install --frozen-lockfile
COPY --chown=bun:bun frontend ./
RUN bun run build

EXPOSE 5173
CMD ["bun", "run", "preview", "--", "--host", "0.0.0.0", "--port", "5173"]
