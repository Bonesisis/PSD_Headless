# Optional isolated runtime; PhotoCraft is supplied separately by the operator.
# This image does not bundle PhotoCraft or customer templates/fonts.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    fontconfig libfontconfig1 libfreetype6 libheif1 liblcms2-2 \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /work/in /work/out /work/.local/share/fonts && chown -R 1000:1000 /work
USER 1000:1000
ENV HOME=/work
CMD ["sleep", "infinity"]
