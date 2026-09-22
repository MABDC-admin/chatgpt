local lib = require('_guardian.lua.lib')

if ngx.req.get_method() ~= 'POST' then
    return lib.json_response(405, {ok = false, error = 'Method not allowed'})
end

local body = lib.parse_body_json()
local token = body.token
local password = body.password

if not token or not password then
    return lib.json_response(400, {ok = false, error = 'Token and password required'})
end

if #password < 6 then
    return lib.json_response(400, {ok = false, error = 'Password must be at least 6 characters'})
end

local result = lib.call_python('verify_token', {token = token, ['type'] = 'reset'})
if not result or not result.ok or not result.data then
    return lib.json_response(400, {ok = false, error = 'Invalid or expired reset token'})
end

lib.call_python('update_password', {user_id = result.data.user_id, password = password})
lib.call_python('delete_tokens_for_user', {user_id = result.data.user_id, ['type'] = 'reset'})

return lib.json_response(200, {ok = true})
