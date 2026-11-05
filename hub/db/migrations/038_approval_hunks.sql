-- Stage 17.2 (D51): a write allowed in part - the hunks of its diff the user chose (a JSON list of their indexes,
-- as the request's preview numbers them); NULL: the whole of it.
ALTER TABLE approvals ADD COLUMN hunks TEXT;
