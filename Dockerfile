# The Jig toolchain in a container: check, test, build and run Jig projects without installing Python.
#   docker build -t jig .
#   docker run --rm -v "$PWD:/work" jig check --pretty .
#   docker run --rm -v "$PWD:/work" jig gate .
FROM python:3.13-slim
WORKDIR /jig
COPY pyproject.toml README.md ./
COPY jig ./jig
RUN pip install --no-cache-dir . pytest
WORKDIR /work
ENTRYPOINT ["jig"]
CMD ["--help"]
