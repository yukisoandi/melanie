#!/bin/bash
rm -rf cookies.txt
source /Users/m/.config/fish/sourcevars.sh

PATH=/Users/m/Opt/mamba/envs/melanie/bin:/Users/m/Opt/mamba/condabin:/usr/local/Cellar/coreutils/9.3/libexec/gnubin:/usr/local/opt/findutils/libexec/gnubin:/opt/local/bin:/usr/local/opt/llvm/bin:/Users/m/.cargo/bin:/usr/local/bin:/System/Cryptexes/App/usr/bin:/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/share/dotnet:~/.dotnet/tools:/usr/local/MacGPG2/bin
TRACK="https://twitter.com/4zamasu/status/1597893905286303746?s=20"
/Users/m/Opt/mamba/envs/melanie/bin/yt-dlp --cookies-from-browser chrome --cookies cookies.txt $TRACK -o /dev/null
/Users/m/Opt/mamba/envs/melanie/bin/fernet encrypt "$(cat cookies.txt)" | redis-cli -h melanie -x set encrypted_cookies
rm -rf cookies.txt
