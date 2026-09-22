local lib = require('_guardian.lua.lib')

local session_token = lib.get_cookie('quipper_session')
if not session_token then
    return lib.json_response(401, {ok = false, error = 'Not authenticated'})
end

local tok_result = lib.call_python('verify_token', {token = session_token, ['type'] = 'session'})
if not tok_result or not tok_result.ok or not tok_result.data then
    return lib.json_response(401, {ok = false, error = 'Invalid session'})
end

if tok_result.data.role ~= 'admin' then
    return lib.json_response(403, {ok = false, error = 'Admin access required'})
end

local uri = ngx.var.uri
local method = ngx.req.get_method()

if uri == '/api/admin/users' and method == 'GET' then
    local result = lib.call_python('list_users', {})
    return lib.json_response(200, result)

elseif uri == '/api/admin/stats' and method == 'GET' then
    local result = lib.call_python('get_stats', {})
    return lib.json_response(200, result)

elseif uri == '/api/admin/approve' and method == 'POST' then
    local body = lib.parse_body_json()
    if not body.id then
        return lib.json_response(400, {ok = false, error = 'User ID required'})
    end
    lib.call_python('approve_user', {id = body.id})
    local user = lib.call_python('get_user_by_id', {id = body.id})
    if user and user.ok and user.data then
        lib.call_python('send_email', {
            to = user.data.email,
            subject = 'Your Quipper account has been approved',
            body = 'Your account has been approved by an administrator. You can now log in at https://quipper.mabdc.com/auth/login.html'
        })
    end
    return lib.json_response(200, {ok = true})

elseif uri == '/api/admin/revoke' and method == 'POST' then
    local body = lib.parse_body_json()
    if not body.id then
        return lib.json_response(400, {ok = false, error = 'User ID required'})
    end
    local user = lib.call_python('get_user_by_id', {id = body.id})
    lib.call_python('revoke_user', {id = body.id})
    if user and user.ok and user.data then
        lib.call_python('send_email', {
            to = user.data.email,
            subject = 'Your Quipper access has been revoked',
            body = 'Your Quipper account access has been revoked by an administrator. Contact admin@mabdc.ae if you believe this was a mistake.'
        })
    end
    return lib.json_response(200, {ok = true})

else
    return lib.json_response(404, {ok = false, error = 'Not found'})
end
