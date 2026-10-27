-- What a confirmed card was confirmed against (D24): git HEAD, the top-level folders, digests of the dependency
-- files - JSON. The hub compares the project with it to say when the card may be outdated. NULL for versions
-- confirmed before this column, and for drafts.
ALTER TABLE project_cards ADD COLUMN baseline TEXT;
