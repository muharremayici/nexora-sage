# Reviewing shared profile code in two React applications

Should the profile-update code in Bulletproof React's Next Pages and React/Vite
applications move into a shared package? SAGE found matching schema, mutation
hook and component records. Its focused tools located the consumer and exposed
the available source context. Reviewing that source produces a practical
sharing checklist, not an automatic approval to merge.

This case combines clone discovery, symbol search, source-bound inspection and
focused State Flow orientation. It uses SAGE v1.4.0 and Bulletproof React commit
[`9506629`](https://github.com/alan2207/bulletproof-react/tree/9506629ed003a561c6627735480cce4994244bb4).
The [selected evidence](corpus_developer_decision_showcase_evidence.json)
records the exact analyzer, target, snapshot, source hashes and limits.

## From matching code to a review checklist

The preserved Clone Detector output contains these pairs in the two acquired
applications. Manual source comparison confirms that both the API file and the
component file are byte-identical across this pair.

| Recorded pair | Source range in each application | Question before sharing |
| --- | --- | --- |
| `updateProfileInputSchema`, cluster 118 | API file, lines 8–13 | Will both callers keep the same input validation? |
| `useUpdateProfile`, cluster 52 | API file, lines 25–40 | Will refreshing user data and forwarding the caller's success callback both remain? |
| `UpdateProfile`, cluster 3 | Component file, lines 13–91 | Will notification, submit-pending and drawer-completion bindings remain? |

The pinned sources are the
[Next Pages API](https://github.com/alan2207/bulletproof-react/blob/9506629ed003a561c6627735480cce4994244bb4/apps/nextjs-pages/src/features/users/api/update-profile.ts#L8-L40)
and [component](https://github.com/alan2207/bulletproof-react/blob/9506629ed003a561c6627735480cce4994244bb4/apps/nextjs-pages/src/features/users/components/update-profile.tsx#L13-L91),
with their [React/Vite API](https://github.com/alan2207/bulletproof-react/blob/9506629ed003a561c6627735480cce4994244bb4/apps/react-vite/src/features/users/api/update-profile.ts#L8-L40)
and [component](https://github.com/alan2207/bulletproof-react/blob/9506629ed003a561c6627735480cce4994244bb4/apps/react-vite/src/features/users/components/update-profile.tsx#L13-L91)
counterparts. Matching records suggest a sharing candidate; they do not establish
that extracting it is appropriate or behavior-preserving.

## What the focused tools add

Symbol search returned two hook definitions and two consumer import-binding
candidates, not four implementations. Focused State Flow orientation selected
`useUpdateProfile` with matching snapshot/live source identity. It recorded
references and import-bound call syntax, but could not resolve the `useUser`
export or the external `useMutation` target. A complete upstream/downstream
state trace was unavailable. File-level mutation context was not promoted to a
symbol-level or executed relationship.

`inspect_file` grounded the Next Pages component in Atlas and returned lines
13–52, explicitly marking lines 53–91 as omitted. It supplied a precise
follow-up range and kept `safe_to_edit_from_inspection` false. The compact
packet supports orientation without pretending that partial source is sufficient
to edit the whole component. Full source was then reviewed separately for the
checklist below.

## Contracts the source review identifies

The API patches `/users/profile`. Its success handler calls the `useUser`
refetch result before forwarding the caller's optional success callback. That
call order is visible in source; completion ordering or cache correctness was
not tested. The component provides a success notification, binds `isPending`
to its submit indicator and `isSuccess` to drawer completion, supplies existing
user fields as defaults and uses the shared input schema.

Sharing those files also requires preserving their surrounding integration.
Both apps obtain `useUser` through `configureAuth`, but their route handling is
different. The Next Pages API client has a browser guard and a server-cookie
helper that the Vite client does not contain. These distinctions come from
manual source review, not resolved State Flow callees. Compare the
[Next Pages auth](https://github.com/alan2207/bulletproof-react/blob/9506629ed003a561c6627735480cce4994244bb4/apps/nextjs-pages/src/lib/auth.tsx),
[Vite auth](https://github.com/alan2207/bulletproof-react/blob/9506629ed003a561c6627735480cce4994244bb4/apps/react-vite/src/lib/auth.tsx),
[Next Pages API client](https://github.com/alan2207/bulletproof-react/blob/9506629ed003a561c6627735480cce4994244bb4/apps/nextjs-pages/src/lib/api-client.ts)
and [Vite API client](https://github.com/alan2207/bulletproof-react/blob/9506629ed003a561c6627735480cce4994244bb4/apps/react-vite/src/lib/api-client.ts).

The resulting decision is to review a shared profile contract with explicit
auth/API integration boundaries, then verify it in each app's native test
environment. No extraction or target-code mutation was performed here.

## Run scope and unresolved results

The analysis process completed: 56 steps completed, none failed. Acquisition
was narrower than configured: `NEXTJS_PAGES` indexed 149 files and `REACT_VITE`
143; configured `MAIN` indexed zero. A generic ownership-remap defect in SAGE
was subsequently repaired in development, but no second run was performed.
This remains a two-application case, not a successful three-project comparison.

The same run's quality gate was **FAIL**: proof-obligation verification was 0.9
against a 0.95 threshold, and one required proof failed against an allowance of
zero. A manual-review-count check also failed but was not enforced in the
non-comparative workspace. These are retained governance results, not
adjudicated Bulletproof React defects.

Merge reported zero candidates and Nanometric Diff was not applicable; this is
not a non-empty Merge demonstration. Test Impact returned no tests in the
selected scope, which does not prove that relevant tests do not exist. No target
tests, components or runtime probes were executed. There is no measured
accuracy, time saving or verified behavior-preservation claim.

Bulletproof React is an independent MIT-licensed project. This case links to
pinned source rather than redistributing its implementation and does not imply
maintainer endorsement. See the wider [claims/evidence matrix](../CLAIMS_EVIDENCE_MATRIX.md)
for the distinction between current product capabilities and roadmap work.
