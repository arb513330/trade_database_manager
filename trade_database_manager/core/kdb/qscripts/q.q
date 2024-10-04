\d .path
mkdir:{[dir] os:.z.o; $[os in `l32`l64; system"mkdir -p ", dir; os in `w32`w64; system"mkdir ", dir; '("Unsupported operating system: ", os)] }
exists:{[p] if[11h=type key p; :1b]; 0b}
pwd: {[] os:.z.o; $[os in `l32`l64; :raze system"pwd"; os in `w32`w64; :raze system":cd"; '("Unsupported operating system: ", os)] }

\d .partable
append_helper:{[d;pardir;t] tcontent:get t; pardir upsert .Q.en[d;tcontent]}
append:{[d;p;t] bdir:.Q.par[d;p;t]; append_helper[d;bdir;t]}
createOrAppend:{[d;p;f;t] bdir:.Q.par[d;p;t]; kbdir:key bdir; if[(11h=type kbdir)&(0<count kbdir); tt:append_helper[d;bdir;t]; :tt]; .Q.dpft[d;p;f;t]}
