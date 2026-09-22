local uri = ngx.var.uri

if uri == '/api/auth/login' then
    return require('_guardian.lua.api_login')
elseif uri == '/api/auth/signup' then
    return require('_guardian.lua.api_signup')
elseif uri == '/api/auth/reset-request' then
    return require('_guardian.lua.api_reset_request')
elseif uri == '/api/auth/reset' then
    return require('_guardian.lua.api_reset')
elseif uri == '/api/auth/logout' then
    return require('_guardian.lua.api_logout')
elseif uri:match('^/api/admin/') then
    return require('_guardian.lua.api_admin')
else
    ngx.status = 404
    ngx.header['Content-Type'] = 'application/json'
    ngx.say('{"ok":false,"error":"Not found"}')
    return ngx.exit(404)
end
