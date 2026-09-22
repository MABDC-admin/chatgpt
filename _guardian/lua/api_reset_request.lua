local lib = require('_guardian.lua.lib')

if ngx.req.get_method() ~= 'POST' then
    return lib.json_response(405, {ok = false, error = 'Method not allowed'})
end

local body = lib.parse_body_json()
local email = body.email

if not email then
    return lib.json_response(400, {ok = false, error = 'Email required'})
end

local user = lib.call_python('get_user_by_email', {email = email})
if user and user.ok and user.data then
    lib.call_python('delete_tokens_for_user', {user_id = user.data.id, ['type'] = 'reset'})
    local tok = lib.call_python('create_token', {user_id = user.data.id, ['type'] = 'reset', ttl = 3600})
    if tok and tok.ok then
        local reset_url = 'https://quipper.mabdc.com/auth/reset.html?token=' .. tok.data.token
        lib.call_python('send_email', {
            to = email,
            subject = 'Reset your Quipper password',
            body = 'Click the link below to reset your password. This link expires in 1 hour.\n\n' .. reset_url .. '\n\nIf you did not request this, ignore this email.'
        })
    end
end

return lib.json_response(200, {ok = true, message = 'If that email exists, a reset link has been sent.'})
