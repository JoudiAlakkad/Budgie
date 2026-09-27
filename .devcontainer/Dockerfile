FROM mcr.microsoft.com/devcontainers/base:ubuntu

# Install basic development tools.
RUN apt-get update \
    && apt-get install -y \
        python3 \
        python3-pip \
        python3-venv \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Use a non-root development user.
USER vscode

# Working directory inside the development container.
WORKDIR /workspace