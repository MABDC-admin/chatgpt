local lib = require('_guardian.lua.lib')

if ngx.req.get_method() ~= 'POST' then
    return lib.json_response(405, {ok = false, error = 'Method not allowed'})
end

local body = lib.parse_body_json()
local email = body.email
local password = body.password

if not email or not password then
    return lib.json_response(400, {ok = false, error = 'Email and password required'})
end

local user, err = lib.call_python('get_user_by_email', {email = email})
if err or not user or not user.ok then
    return lib.json_response(500, {ok = false, error = 'Internal error'})
end
if not user.data then
    return lib.json_response(401, {ok = false, error = 'Invalid email or password'})
end

local check, err = lib.call_python('verify_password', {password = password, hash = user.data.password_hash})
if err or not check or not check.ok or not check.data or not check.data.valid then
    return lib.json_response(401, {ok = false, error = 'Invalid email or password'})
end

if user.data.status == 'pending' then
    return lib.json_response(403, {ok = false, error = 'Account pending approval'})
end
if user.data.status == 'revoked' then
    return lib.json_response(403, {ok = false, error = 'Account has been revoked'})
end

lib.call_python('delete_tokens_for_user', {user_id = user.data.id, ['type'] = 'session'})

local tok, err = lib.call_python('create_token', {user_id = user.data.id, ['type'] = 'session', ttl = 604800})
if err or not tok or not tok.ok then
    return lib.json_response(500, {ok = false, error = 'Failed to create session'})
end

ngx.header['Set-Cookie'] = 'quipper_session=' .. tok.data.token ..
    '; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=604800'

return lib.json_response(200, {ok = true, data = {role = user.data.role, email = user.data.email}})
