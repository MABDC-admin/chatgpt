local lib = require('_guardian.lua.lib')

local uri = ngx.var.uri

if uri:match('^/auth/') or uri:match('^/api/') then
    return
end

local session_token = lib.get_cookie('quipper_session')
if not session_token then
    return ngx.redirect('/auth/login.html')
end

local result = lib.call_python('verify_token', {token = session_token, ['type'] = 'session'})
if not result or not result.ok or not result.data then
    ngx.header['Set-Cookie'] = 'quipper_session=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0'
    return ngx.redirect('/auth/login.html')
end

if result.data.status ~= 'approved' then
    ngx.header['Set-Cookie'] = 'quipper_session=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0'
    return ngx.redirect('/auth/pending.html')
end
