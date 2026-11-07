# syntax=docker/dockerfile:1
#
# Multi-stage, framework-dependent publish.
#
# The build stage uses an official SDK image. The final stage uses an official ASP.NET
# runtime image, and THAT image's runtime is what ships. A green build on a patched SDK
# says nothing about the runtime in the final image; see README.md "What ships".

ARG SDK_IMAGE=mcr.microsoft.com/dotnet/sdk:10.0.403
ARG RUNTIME_IMAGE=mcr.microsoft.com/dotnet/aspnet:10.0.14

FROM ${SDK_IMAGE} AS build
WORKDIR /src
COPY src/CookieApi/CookieApi.csproj src/CookieApi/
RUN dotnet restore src/CookieApi/CookieApi.csproj
COPY src/ src/
RUN dotnet publish src/CookieApi/CookieApi.csproj -c Release -o /app/publish --no-self-contained

FROM ${RUNTIME_IMAGE} AS final
WORKDIR /app
COPY --from=build /app/publish .
ENV ASPNETCORE_HTTP_PORTS=8080
EXPOSE 8080
USER $APP_UID
ENTRYPOINT ["dotnet", "CookieApi.dll"]
