Vonage Python server SDK samples


The Vonage Python SDK (vonage) contains methods and data models to help you use many of Vonage's APIs. It also includes support for the new mobile network APIs announced by Vonage.

Here are some key changes to the SDK:

1. v4 of the Vonage Python SDK now uses a monorepo structure, with different packages for calling different Vonage APIs all using common code. You don't need to install the different packages directly as the top-level vonage package pulls them in and provides a common and consistent way to access methods.
2. The v4 SDK makes heavy use of Pydantic data models to make it easier to call Vonage APIs and parse the results. This also enforces correct typing and makes it easier to pass the right objects to Vonage.
3. Docstrings have been added to methods and data models across the whole SDK to increase quality-of-life developer experience and make in-IDE development easier.
4. Many new custom errors have been added for finer-grained debugging. Error objects now contain more information and error messages give more information and context.
5. Support has been added for all Vonage Video API features, bringing it to feature parity with the OpenTok package. See the OpenTok -> Vonage Video migration guide for migration assistance. If you're using OpenTok, migration to use v4 of the Vonage Python SDK rather than the opentok Python package is highly recommended.
6. APIs that have been deprecated by Vonage, e.g. Meetings API, have not been implemented in v4. Objects deprecated in v3 of the SDK have also not been implemented in v4.