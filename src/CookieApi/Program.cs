using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.Cookies;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddControllers();

builder.Services
    .AddAuthentication(CookieAuthenticationDefaults.AuthenticationScheme)
    .AddCookie(options =>
    {
        options.LoginPath = "/login";
        options.AccessDeniedPath = "/denied";
        options.Cookie.Name = "demo.auth";
        options.Cookie.HttpOnly = true;
        options.Cookie.SameSite = SameSiteMode.Lax;
        // The fixture is served over plain HTTP on loopback; a real deployment sits behind TLS.
        options.Cookie.SecurePolicy = CookieSecurePolicy.SameAsRequest;
        options.ExpireTimeSpan = TimeSpan.FromMinutes(30);
    });

builder.Services.AddAuthorization();

var app = builder.Build();

app.UseAuthentication();
app.UseAuthorization();

app.MapGet("/healthz", () => Results.Text("ok", "text/plain"));

app.MapGet("/", (HttpContext ctx) =>
{
    var name = ctx.User.Identity?.IsAuthenticated == true ? ctx.User.Identity.Name : null;
    var body = new StringBuilder();
    body.Append("<!doctype html><html><head><title>Cookie demo</title></head><body>");
    body.Append("<h1>Cookie authentication demo</h1>");
    if (name is null)
    {
        body.Append("<p>You are signed out. <a href=\"/login\">Sign in</a> to read <a href=\"/api/secret\">the protected JSON</a>.</p>");
    }
    else
    {
        body.Append("<p>Signed in as <strong>").Append(Html(name)).Append("</strong>. ");
        body.Append("<a href=\"/api/secret\">Read the protected JSON</a>.</p>");
        body.Append("<form method=\"post\" action=\"/logout\"><button type=\"submit\">Sign out</button></form>");
    }
    body.Append("</body></html>");
    return Results.Text(body.ToString(), "text/html");
});

app.MapGet("/login", (string? returnUrl) => Results.Text(LoginPage(returnUrl, error: null), "text/html"));

app.MapPost("/login", async (HttpContext ctx, IConfiguration config) =>
{
    // The form is read by hand rather than bound, so the fixture stays a single file
    // without the antiforgery middleware. It is a loopback test fixture, not a product.
    var form = await ctx.Request.ReadFormAsync();
    var username = form["username"].ToString();
    var password = form["password"].ToString();
    var returnUrl = form["returnUrl"].ToString();

    var expectedUser = config["Demo:Username"] ?? "demo";
    var expectedPassword = config["Demo:Password"] ?? "";

    if (!FixedTimeEquals(username, expectedUser) || !FixedTimeEquals(password, expectedPassword))
    {
        return Results.Text(LoginPage(returnUrl, error: "Unknown user name or password."), "text/html", statusCode: 401);
    }

    var claims = new[] { new Claim(ClaimTypes.Name, expectedUser) };
    var identity = new ClaimsIdentity(claims, CookieAuthenticationDefaults.AuthenticationScheme);
    await ctx.SignInAsync(CookieAuthenticationDefaults.AuthenticationScheme, new ClaimsPrincipal(identity));

    return Results.Redirect(IsLocalUrl(returnUrl) ? returnUrl : "/");
});

app.MapPost("/logout", async (HttpContext ctx) =>
{
    await ctx.SignOutAsync(CookieAuthenticationDefaults.AuthenticationScheme);
    return Results.Redirect("/");
});

app.MapGet("/denied", () => Results.Text("<!doctype html><html><body><h1>Access denied</h1></body></html>", "text/html", statusCode: 403));

app.MapControllers();

app.Run();

static string LoginPage(string? returnUrl, string? error)
{
    var sb = new StringBuilder();
    sb.Append("<!doctype html><html><head><title>Sign in</title></head><body>");
    sb.Append("<h1>Sign in</h1>");
    if (error is not null)
    {
        sb.Append("<p class=\"error\">").Append(Html(error)).Append("</p>");
    }
    sb.Append("<form id=\"sign-in\" method=\"post\" action=\"/login\">");
    sb.Append("<label>User name <input name=\"username\" autocomplete=\"username\"></label><br>");
    sb.Append("<label>Password <input name=\"password\" type=\"password\" autocomplete=\"current-password\"></label><br>");
    sb.Append("<input type=\"hidden\" name=\"returnUrl\" value=\"").Append(Html(IsLocalUrl(returnUrl) ? returnUrl! : "/")).Append("\">");
    sb.Append("<button type=\"submit\">Sign in</button>");
    sb.Append("</form></body></html>");
    return sb.ToString();
}

static bool IsLocalUrl(string? url) =>
    !string.IsNullOrEmpty(url)
    && url[0] == '/'
    && (url.Length == 1 || (url[1] != '/' && url[1] != '\\'));

static bool FixedTimeEquals(string a, string b) =>
    CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(a), Encoding.UTF8.GetBytes(b));

static string Html(string s) => System.Net.WebUtility.HtmlEncode(s);
