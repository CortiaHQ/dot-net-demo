using Microsoft.AspNetCore.Authentication.Cookies;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace CookieApi.Controllers;

/// <summary>
/// The protected JSON endpoint. It is a known API endpoint because of
/// <see cref="ApiControllerAttribute"/>, and it is protected by the cookie scheme only.
/// </summary>
[ApiController]
[Route("api/secret")]
[Authorize(AuthenticationSchemes = CookieAuthenticationDefaults.AuthenticationScheme)]
public sealed class SecretController : ControllerBase
{
    private readonly IConfiguration _config;

    public SecretController(IConfiguration config)
    {
        _config = config;
    }

    [HttpGet]
    public IActionResult Get()
    {
        return Ok(new SecretResponse(
            User: User.Identity?.Name ?? "",
            Secret: _config["Demo:Secret"] ?? "",
            IssuedAt: DateTimeOffset.UtcNow));
    }

    public sealed record SecretResponse(string User, string Secret, DateTimeOffset IssuedAt);
}
