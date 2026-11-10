FROM public.ecr.aws/docker/library/eclipse-temurin:25.0.5_8-jdk-noble@sha256:95e350ad1eb738f943e141640f7e3d797e483359a3b33c3864255bfd1ad71f8a AS java

FROM public.ecr.aws/docker/library/sonarqube:26.9.0.129388-community@sha256:c0f1160bccfa435db4168c2d7df69af3c3ea7bfeb87395613014048fb33a68c1

LABEL org.opencontainers.image.source="https://github.com/panixida-infrastructure/core-platform" \
      org.opencontainers.image.description="SonarQube 26.9 with Temurin 25.0.5 for the pinned Community Branch Plugin" \
      org.opencontainers.image.version="26.9.0.129388-java25.0.5"

USER root
RUN rm -rf /opt/java/openjdk
COPY --from=java /opt/java/openjdk/ /opt/java/openjdk/

# Preserve the official SonarQube image's Java security settings.
RUN echo 'networkaddress.cache.ttl=5' >> /opt/java/openjdk/conf/security/java.security \
    && sed -i 's?securerandom.source=file:/dev/random?securerandom.source=file:/dev/urandom?g' \
       /opt/java/openjdk/conf/security/java.security

USER sonarqube
RUN java -XX:TrimNativeHeapInterval=300000 -version
