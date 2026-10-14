library(ggplot2)
out <- commandArgs(trailingOnly=TRUE)[1]
d <- read.csv(file.path(out,'waveforms.csv'))
spec <- list(
 source38=list(panel='Study 38: first raw cycle (s)',title='Study 38: raw cycle',colour='#779FB2',x='Time (s)',median=FALSE),
 source120=list(panel='Gait120: first released envelope (sample)',title='Gait120: released envelope',colour='#B78D62',x='Source sample',median=FALSE),
 median38=list(panel='Study 38: five-cycle median',title='5-cycle median',colour='#779FB2',x='Gait cycle (%)',median=TRUE),
 median120=list(panel='Gait120: ten-cycle median',title='10-cycle median',colour='#B78D62',x='Gait cycle (%)',median=TRUE))
for (name in names(spec)) {
 s <- spec[[name]]; z <- subset(d,panel==s$panel)
 p <- ggplot()
 if (s$median) p <- p + geom_line(data=subset(z,curve!='median'),aes(x,value,group=curve),colour='grey80',linewidth=.18)
 front <- subset(z,curve==if(s$median)'median' else 'source')
 p <- p + geom_line(data=front,aes(x,value),colour=s$colour,linewidth=if(s$median).5 else .23)+
  labs(title=s$title,x=s$x,y=NULL)+expand_limits(y=0)+
  scale_y_continuous(breaks=if(s$median)c(0,1) else scales::breaks_pretty(n=2),expand=expansion(mult=.05))+
  scale_x_continuous(breaks=if(s$median)c(0,50,100) else scales::breaks_pretty(n=3),expand=expansion(mult=c(.01,.07)))+
  theme_classic(base_size=7.5,base_family='Helvetica')+
  theme(plot.title=element_text(size=7.5,face='bold',margin=margin(b=1)),
        axis.title=element_text(size=7.5,margin=margin(t=0)),axis.text=element_text(size=7.5,colour='black'),
        axis.ticks.length=grid::unit(1,'mm'),axis.line=element_line(linewidth=.2),
        plot.margin=margin(1,5,1,1))
 ggsave(file.path(out,paste0(name,'.pdf')),p,width=55,height=28,units='mm',device='pdf',useDingbats=FALSE)
}
