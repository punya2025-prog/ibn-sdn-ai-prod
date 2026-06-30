# generate self-signed cert (use proper CA cert in production)
mkdir -p ~/ibn-sdn-ai-prod/certs
openssl req -x509 -newkey rsa:4096 \
  -keyout ~/ibn-sdn-ai-prod/certs/key.pem \
  -out ~/ibn-sdn-ai-prod/certs/cert.pem \
  -days 365 -nodes \
  -subj "/CN=192.168.20.15/O=IBN-Gateway"

# update main.py to use SSL
# change uvicorn.run to:
# uvicorn.run("main:app",
#   host="0.0.0.0", port=8443,
#   ssl_keyfile="certs/key.pem",
#   ssl_certfile="certs/cert.pem")
