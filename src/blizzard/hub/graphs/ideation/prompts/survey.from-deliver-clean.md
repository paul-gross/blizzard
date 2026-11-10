# Survey — after clean delivery failed

Read `blizzard runner artifact get survey --content`, `blizzard runner artifact get survey-delta --content`,
`blizzard runner artifact get garden-delivery-failure --content`, and `blizzard runner chunk history`. Count transitions
out of `deliver-clean`, including this one. If two or more appear, escalate with `blizzard runner ask` instead of
retrying. Otherwise correct any reported skeleton shape fault and republish the survey and `survey-delta`, without
changing the sweep's conclusions. Choose the same survey outcome to retry delivery.
