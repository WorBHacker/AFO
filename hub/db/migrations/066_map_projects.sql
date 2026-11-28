-- «Стабилизация», portion 5 (the user's complaint 3 of 2026-11-27): the projects of a chat's map, as the user ticked
-- them (a JSON list of project ids); NULL - the chat's own projects and those a confirmed link joins to them.
ALTER TABLE conversation_context ADD COLUMN map_projects TEXT;
