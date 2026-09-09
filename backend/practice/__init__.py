"""Practice-quiz generation: grounded multiple-choice questions per unit objective.

The correct answer is determined and stored server-side and is never included in
the payload sent to the client before the student answers. The client only receives
the correct option + explanation AFTER it POSTs a chosen option, so the server (the
sole holder of the answer key) is the only thing that grades attempts. This keeps
weak-spot / mastery data trustworthy.
"""
