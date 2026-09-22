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

if #password < 6 then
    return lib.json_response(400, {ok = false, error = 'Password must be at least 6 characters'})
end

local existing = lib.call_python('get_user_by_email', {email = email})
if existing and existing.ok and existing.data then
    return lib.json_response(409, {ok = false, error = 'Email already registered'})
end

local result = lib.call_python('create_user', {email = email, password = password})
if not result or not result.ok then
    local msg = (result and result.error) or 'Registration failed'
    return lib.json_response(400, {ok = false, error = msg})
end

lib.call_python('send_email', {
    to = 'sottodennis@gmail.com',
    subject = 'New Quipper access request from ' .. email,
    body = 'A new user has signed up for Quipper access.\n\nEmail: ' .. email .. '\n\nPlease log in to the admin dashboard to approve or reject this request.\nhttps://quipper.mabdc.com/auth/admin.html'
})

return lib.json_response(200, {ok = true})
