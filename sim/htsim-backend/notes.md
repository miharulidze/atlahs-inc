### Main Idea 

we will focus on traffic matrices. We'll begin my
creating a modified .cm format to support Multicast groups such
that we can begin to work inside the htsim-backend. There we'll
begin by propagating the mcast command into the uec_src code and
there will start with the P2P dumb implementation of mcast (sending 
sequentially P2P for each destination in the mcast group), time it and
expect to receive a linear scaling between mcast completion time
and mcast group size. Then we'll continue later by adding
switch logic to support real multicast with the final goal to
receive a constant completion time, regardless of the group
size. 






### IGNORE
Nodes 64
NUM_GRPS 2
1,54,32,12,45
23,62,22,12,23,34,45
Connections 8
mc 1->1  (root = 62 mcast to group 1)
0->15 start 0 size 2000000
1->15 start 0 size 2000000
2->15 start 0 size 2000000
3->15 start 0 size 2000000
4->15 start 0 size 2000000
5->15 start 0 size 2000000
6->15 start 0 size 2000000
7->15 start 0 size 2000000