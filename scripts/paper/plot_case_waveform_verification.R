library(ggplot2)
out <- commandArgs(trailingOnly=TRUE)[1]
d <- read.csv(file.path(out,'waveforms.csv'))
d$panel <- factor(d$panel,levels=c('Study 38: first raw cycle (s)',
 'Gait120: first released envelope (sample)','Study 38: five-cycle median','Gait120: ten-cycle median'))
single <- subset(d,curve=='source')
cycles <- subset(d,grepl('cycle-',curve))
med <- subset(d,curve=='median')
p <- ggplot() +
 geom_line(data=cycles,aes(x,value,group=curve),colour='grey75',linewidth=.3)+
 geom_line(data=single,aes(x,value),colour='#527D91',linewidth=.35)+
 geom_line(data=med,aes(x,value),colour='#255D75',linewidth=.85)+
 facet_wrap(~panel,ncol=2,scales='free')+
 labs(title='Recovered public EMG waveforms: right tibialis anterior',
 subtitle='Top: source data in native scale. Bottom: individual cycles (grey), participant median (blue).',
 x=NULL,y=NULL,
 caption='Bottom x: gait cycle (%); y: cycle-max normalized signal. No amplitude pooling.\nStudy 38 / subject 1 and Gait120 / S011; original preprocessing re-executed, not synthetic curves.')+
 theme_classic(base_size=11,base_family='sans')+
 theme(strip.background=element_blank(),strip.text=element_text(face='bold',hjust=0),
 axis.text=element_text(colour='black'),panel.spacing=grid::unit(8,'mm'),
 plot.title=element_text(face='bold'),plot.caption=element_text(hjust=0,size=9),
 plot.margin=margin(12,16,12,12))
ggsave(file.path(out,'waveform_verification_v001.png'),p,width=240,height=160,units='mm',dpi=220,device=ragg::agg_png)
