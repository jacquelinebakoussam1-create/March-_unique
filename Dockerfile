FROM python:3.12-slim
WORKDIR /app
COPY serveur.py index.html marche_unique.sql ./
# La base vit dans /data : montez-y un disque permanent chez l'hébergeur.
ENV HOST=0.0.0.0 PORT=8000 BASE=/data/marche_unique.db EXEMPLES=0
EXPOSE 8000
CMD ["python3", "serveur.py"]
