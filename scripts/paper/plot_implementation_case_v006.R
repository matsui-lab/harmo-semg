library(ggplot2)
out <- commandArgs(trailingOnly=TRUE)[1]
d <- read.csv(file.path(out, 'timing.csv'))
d$subject <- factor(d$subject, levels=c('Gait120 / S011','Study 38 / 1'))
p <- ggplot(d, aes(centroid, subject, colour=subject)) +
  geom_segment(aes(x=0,xend=100,yend=subject),colour='grey88',linewidth=0.3) +
  geom_point(size=2.5) +
  geom_text(aes(label=sprintf('%.1f',centroid)),nudge_y=0.25,size=2.7,colour='black') +
  scale_colour_manual(values=c('Gait120 / S011'='#EDCAA5','Study 38 / 1'='#A8CADB')) +
  scale_x_continuous(limits=c(0,100),breaks=c(0,50,100),expand=expansion(mult=c(.02,.02))) +
  scale_y_discrete(expand=expansion(add=c(.35,.65))) +
  labs(x='Activation centroid\n(% gait cycle)',y=NULL) +
  theme_classic(base_size=8,base_family='Helvetica') +
  theme(legend.position='none',axis.line.y=element_blank(),axis.ticks.y=element_blank(),
        axis.text=element_text(colour='black',size=7.5),axis.title=element_text(size=8),
        plot.margin=margin(2,9,1,1))
ggsave(file.path(out,'timing_plot.pdf'),p,width=56,height=42,units='mm',device='pdf',useDingbats=FALSE)
