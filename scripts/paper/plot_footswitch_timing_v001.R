args <- commandArgs(trailingOnly=TRUE)
if (length(args) != 1L) stop("diagnostic directory required")
root <- args[1]
suppressPackageStartupMessages(library(ggplot2))
curves <- read.csv(file.path(root,"curves.csv"), stringsAsFactors=FALSE)
waves <- read.csv(file.path(root,"source_EMG_extrema_v002.csv"), stringsAsFactors=FALSE)
foot <- read.csv(file.path(root,"source_footswitch_full_v002.csv"), stringsAsFactors=FALSE)
record_label <- function(x) sub(".hea$", "", basename(x))
curves$record <- record_label(curves$record)
curves$muscle <- ifelse(curves$muscle=="tibialis anterior", "Tibialis anterior", "Lateral gastrocnemius")
curves$side <- ifelse(curves$side=="left", "Left", "Right")
curves$anchor <- factor(curves$anchor, levels=c("lower-component","upper-component"),
                        labels=c("Lower electrical component", "Upper electrical component"))
base <- theme_classic(base_size=11) + theme(text=element_text(colour="black"),
  axis.text=element_text(colour="black"), strip.background=element_blank(),
  strip.text=element_text(colour="black",face="bold"), legend.position="bottom",
  plot.title=element_text(face="bold"), plot.subtitle=element_text(size=10),
  panel.spacing=grid::unit(.8,"lines"))
palette <- c("#6D99BF", "#C79982")
g <- ggplot(curves, aes(phase, median, colour=record, fill=record)) +
  geom_ribbon(aes(ymin=q25,ymax=q75),colour=NA,alpha=.17) +
  geom_line(linewidth=.55) +
  facet_grid(muscle+side ~ anchor) +
  scale_colour_manual(values=palette) + scale_fill_manual(values=palette) +
  scale_x_continuous(breaks=seq(0,100,25)) +
  scale_y_continuous(limits=c(0,1.05),breaks=c(0,.5,1)) +
  labs(x="Electrical-event phase (%)",y="Within-cycle peak-scaled RMS envelope",colour=NULL,fill=NULL,
       title="Recorded EMG patterns depend on the electrical phase reference",
       subtitle="Diagnostic only: event identity is unverified; lines = cycle medians, bands = cycle IQRs") + base
ggsave(file.path(root,"timing_curves_v002.png"),g,width=11.5,height=9.3,dpi=180,bg="white")
ggsave(file.path(root,"timing_curves_v002.svg"),g,width=11.5,height=9.3,device=svglite::svglite,bg="white")
waves$record <- record_label(waves$record)
waves$side <- ifelse(waves$side=="left","Left","Right")
waves$signal <- paste0(ifelse(waves$muscle=="tibialis anterior","Tibialis anterior","Lateral gastrocnemius")," (",waves$unit,")")
foot$record <- record_label(foot$record)
foot$side <- ifelse(foot$side=="left","Left","Right")
foot$signal <- "Footswitch (V)"
signals <- c("Footswitch (V)","Tibialis anterior (uV)","Lateral gastrocnemius (uV)")
waves$signal <- factor(waves$signal,levels=signals);foot$signal <- factor(foot$signal,levels=signals)
w <- ggplot() +
  geom_linerange(data=waves,aes(time,ymin=minimum,ymax=maximum,colour="Recorded EMG"),linewidth=.2) +
  geom_line(data=waves,aes(time,envelope,colour="RMS envelope",group=interaction(record,side,signal)),linewidth=.3) +
  geom_step(data=foot,aes(time,value,colour="Footswitch",group=interaction(record,side)),linewidth=.27) +
  facet_grid(signal ~ record+side,scales="free_y") +
  scale_colour_manual(values=c("Footswitch"="#303030","Recorded EMG"="#B9B9B9","RMS envelope"="#63949C")) +
  labs(x="Recording time (s)",y=NULL,colour=NULL,
       title="Source footswitch and EMG signals: first 10 seconds",
       subtitle="Raw EMG: extrema in 5-sample bins; footswitch: every 1500 Hz source sample; analysis uses all samples") + base +
  theme(strip.text=element_text(size=9))
ggsave(file.path(root,"source_traces_v002.png"),w,width=13,height=7.3,dpi=180,bg="white")
ggsave(file.path(root,"source_traces_v002.svg"),w,width=13,height=7.3,device=svglite::svglite,bg="white")
