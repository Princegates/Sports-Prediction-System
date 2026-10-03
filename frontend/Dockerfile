FROM node:22-slim AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

FROM node:22-slim
WORKDIR /app
RUN npm install -g serve
COPY --from=build /app/dist ./dist

# Runs as a non-root user rather than the image's default root -- this is
# only a static file server, but there's no reason it should have more
# privilege inside the container than serving ./dist needs.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 4173
CMD ["serve", "-s", "dist", "-l", "4173"]
