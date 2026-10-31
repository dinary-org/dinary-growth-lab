---
name: performance-review
description: Pull metrics for our published videos (YouTube Analytics, Instagram Graph), compare them with each concept's pre-registered hypothesis, separate raw reach from audience quality, and feed wins/losses back into opportunity ranking. Use on a schedule or when asked how our videos performed.
---

# Performance review

1. Each published post must be registered with its hypothesis
   (`dgl post-register ... --hypothesis '{"topic_cluster":..,"format_cluster":..,"statement":..,"predicted_views_range":[lo,hi]}'`).
2. Snapshot metrics with `dgl.feedback.performance.youtube_post_metrics` / `instagram_post_metrics`
   (needs the owner OAuth / Graph tokens in `.env.example`), then `record_snapshot`.
3. `.venv/bin/dgl feedback` — report per post: views vs account median, audience quality
   (follows per 1k views, share rate, save rate, avg view %), and whether the prediction range hit.
4. Language: results are correlational unless the post carried an `ab_group`. Do not claim a
   format "caused" growth from single posts.
5. The `cells` win/loss counts are the `OwnHistory` priors for the next `dgl rank`.
