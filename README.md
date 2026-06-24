# SVN-Replicator
This project aims to provide a tool to continuously mirror svn repos into a git repository. I've developed this since the existing tools I found are either expensive or do not fulfill my expectations on how the history of the original svn repository should be conserved through mirroring.

To use this tool you set up a config for an SVN repository you want to mirror once. Regularly calling this tool will create a git repository that mirrors the SVN repository, including the history. It will keep the mirrored repo up to date automatically. There is the option to automatically push the git repo on a change as well.

## Contributing
If you want to contribute, I'm happy to include your suggestions. A good starting off point would be to look at the open issues, or to create one for your idea. 

## Known issues:
- `client.info()` reports a parsing error, when the call to the local svn instance results in an error. In those cases the result looks like following: 
    ``` xml
    svn: warning: cannot set LC_CTYPE locale
    svn: warning: environment variable LANG is en_US.UTF-8
    svn: warning: please check that your locale name is correct
    <?xml version="1.0" encoding="UTF-8"?>
    ...
    ```
    This can be fixed by calling `sudo dpkg-reconfigure locales` and selecting an appropriate language (i.e. `C.UTF-8`).
