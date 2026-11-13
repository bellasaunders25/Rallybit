FROM debian:bookworm-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake curl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /build
# whisper.cpp v1.9.5, pinned rather than following a moving branch.
RUN curl -fL --retry 3 https://codeload.github.com/ggml-org/whisper.cpp/tar.gz/d1be6fde11ac6e0407606b4e42fe72d34add8037 -o source.tar.gz && tar -xzf source.tar.gz --strip-components=1 && rm source.tar.gz
RUN cmake -S . -B output -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DWHISPER_BUILD_TESTS=OFF -DGGML_NATIVE=OFF -DGGML_OPENMP=OFF -DWHISPER_CURL=OFF && cmake --build output --target whisper-server --parallel 1
RUN mkdir /models && curl -fL --retry 3 https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en-q5_1.bin -o /models/model.bin && echo 'c77c5766f1cef09b6b7d47f21b546cbddd4157886b3b5d6d4f709e91e66c7c2b  /models/model.bin' | sha256sum -c -

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates libstdc++6 libatomic1 && rm -rf /var/lib/apt/lists/* && useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin cvms
COPY --from=build /build/output/bin/whisper-server /usr/local/bin/whisper-server
COPY --from=build /models/model.bin /models/model.bin
COPY --from=build /build/LICENSE /usr/share/doc/cvms/whisper-license.txt
USER cvms
WORKDIR /tmp
EXPOSE 8081
HEALTHCHECK --interval=30s --timeout=4s --start-period=90s CMD curl -fsS http://127.0.0.1:8081/health >/dev/null || exit 1
ENTRYPOINT ["whisper-server"]
CMD ["--host","0.0.0.0","--port","8081","--model","/models/model.bin","--threads","1","--processors","1","--language","en","--no-gpu","--audio-ctx","512","--best-of","1","--beam-size","1","--suppress-nst"]
