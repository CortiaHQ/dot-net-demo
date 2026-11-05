# dot-net-demo

A small ASP.NET Core app that ships in a Linux container, built so that a security
repair can be demonstrated end to end: read a public advisory, upgrade the affected
runtime, resolve the compatibility regression the upgrade causes, and prove that the fix
is in the image a team would actually ship.

The app is deliberately small. It has cookie authentication, one protected JSON endpoint
marked `[ApiController]`, and a tiny client that requests it. Everything in this
repository is new sample code written for the demo; the data and credentials are
synthetic test values.

## The user-visible contract

- A **signed-out** user who asks for the protected JSON is sent to the local sign-in page.
- A **signed-in** user retrieves the JSON.
- An unauthenticated response never contains the protected data.

The client (`client/democlient.py`) makes a plain navigation request, without the
`X-Requested-With: XMLHttpRequest` header, and looks at the first response before it
follows any redirect. On .NET 8 that first response is a `302` to `/login`, and the
client relies on it.

## Layout

| Path | What it is |
| --- | --- |
| `src/CookieApi/` | The ASP.NET Core app (`Program.cs`, `Controllers/SecretController.cs`) |
| `Dockerfile` | Multi-stage, framework-dependent publish; the final stage is an official ASP.NET runtime image |
| `client/democlient.py` | The tiny client, standard library only |
| `tests/test_image.py` | Integration checks that run against the **running image** |
| `tools/evaluate.py` | Builds the image, records the runtime inside it, runs the checks, cleans up |
| `.github/workflows/verify.yml` | Runs the same build and checks on pull requests and on demand |

## Run it

You need Docker and Python 3.10 or newer. Nothing else is installed on the host; the .NET
SDK runs inside the build stage.

Build the image and record what is inside it:

```bash
python tools/evaluate.py build
```

Run the checks against the running image (starts the container on `127.0.0.1:8080`,
runs the tests, stops and removes the container, also on failure):

```bash
python tools/evaluate.py check
```

Remove the image when you are done:

```bash
python tools/evaluate.py clean
```

Each command writes its evidence under `evidence/<label>/` (default label `local`;
pass `--label` to keep several runs apart). `build.json` records the commit, the runtime
image the Dockerfile names, the digest that tag resolved to for `linux/amd64`, the final
image ID, and `dotnet --list-runtimes` as run **inside the final image**. `checks.json`
and `checks.txt` record the test output against that same image ID.

To poke at the app by hand, start it yourself:

```bash
docker run --rm -p 127.0.0.1:8080:8080 cookie-demo:local
python client/democlient.py secret
python client/democlient.py --login demo demo-password-not-real secret
```

The test user is `demo` / `demo-password-not-real` (see `src/CookieApi/appsettings.json`).
The container binds to loopback only.

## What ships

The final image's runtime is what runs in production, and only an inspection of the
**final** image can establish it:

```bash
docker run --rm --entrypoint dotnet cookie-demo:local --list-runtimes
```

A changed project file, or a green build on a patched SDK image, says nothing about the
runtime in the final image. `tools/evaluate.py build` records the inventory together with
the image ID and the commit so that a reviewer can tie them together.

## Scope

- One app, one container, one test suite.
- The app is a loopback test fixture, not a product. It reads its sign-in form by hand
  and has no antiforgery token, TLS, or user store.
- The checks verify the published runtime version and the app's behavior. They do not
  carry an exploit payload.

## License

MIT, see `LICENSE`.
