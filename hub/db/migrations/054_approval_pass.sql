-- 0.1.17, «Недавно заблокировано»: what a trusted chat's rules refused (approvals.decided_by 'rules') the user may
-- let through from the window. 'open' - the chat's next same request goes unasked, once; 'used' - it has gone.
ALTER TABLE approvals ADD COLUMN pass TEXT CHECK (pass IN ('open', 'used'));
