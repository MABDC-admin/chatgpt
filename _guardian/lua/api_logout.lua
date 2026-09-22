local lib = require('_guardian.lua.lib')

local session_token = lib.get_cookie('quipper_session')
if session_token then
    local result = lib.call_python('verify_token', {token = session_token, ['type'] = 'session'})
    if result and result.ok and result.data then
        lib.call_python('delete_tokens_for_user', {user_id = result.data.user_id, ['type'] = 'session'})
    end
end

ngx.header['Set-Cookie'] = 'quipper_session=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0'
return lib.json_response(200, {ok = true})
