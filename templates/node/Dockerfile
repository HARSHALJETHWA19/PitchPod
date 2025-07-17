FROM node:18

RUN apt-get update && apt-get install -y curl git

# Install code-server
RUN curl -fsSL https://code-server.dev/install.sh | sh

ENV PASSWORD=harshal123

EXPOSE 8443

CMD ["code-server", "--bind-addr", "0.0.0.0:8443", "--auth", "password", "/workspace"]