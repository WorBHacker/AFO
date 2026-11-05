-- Stage 11.7 (D44; the user's plan, idea 5 «dynamic model tiering»): a goal's strong model - its first turn (the
-- plan), an investigation and the fix after it, the turn after going back to a checkpoint; the other turns are the
-- worker's (`model_id`). Only when the contract names one: left out, one model works every turn.
ALTER TABLE goals ADD COLUMN strong_model_id TEXT;
