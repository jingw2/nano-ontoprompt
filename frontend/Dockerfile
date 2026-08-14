# E0-IMAGES: digest-pinned frontend production image.
# Exact process command: the built bundle is served via the Vite preview
# server (production build, no dev server).  The frontend is Node — the
# guarded-python-launcher contract applies to the Python service roles.
FROM node:22-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build
EXPOSE 4173
CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0"]
