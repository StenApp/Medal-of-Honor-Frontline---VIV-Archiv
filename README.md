A try to unpack MOHF stuff:

This repo tries to unpack and document the viv archive format used for MOH frontline

-viv -->archive format (comp.viv, blog*.viv and level.viv are similar to BIGF but different) use my unpacker moh_viv_gui.py, for 
shell.viv is BIGF format use https://github.com/Aleksei-Miller/vivtool

-mus +mpf  -->music file, mus can be renamed in asf and be played

-abk+ast  -->sound banks and a streamed audio file ast, abk can contain references to ast or standalone audio files

-mpc -->video file

-ssh PS2, xsh XBOX -->pictures (use [EA Graphics Manager](https://github.com/bartlomiejduda/EA-Graphics-Manager))
